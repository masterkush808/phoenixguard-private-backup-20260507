from __future__ import annotations

import base64
import gzip
import json
import os
import pickle
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence, cast

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from phoenixguard.simulation.masked_future_v3 import (
    ExtractedSequenceV3,
    assign_grouped_folds_v3,
    enforce_disk_reserve,
    group_sequence_families_v3,
)
from phoenixguard.study.event_windows_v3 import (
    FEATURE_NAMES,
    build_event_window_v3,
    feature_vector_v3,
    sequence_tensor_v3,
)
from phoenixguard.study.leakage_audit_v3 import (
    assert_leakage_audit_v3,
    audit_optimized_windows_v3,
)
from phoenixguard.study.optimized_targets_v3 import (
    DEFAULT_OPTIMIZED_HORIZONS,
    build_optimized_targets_v3,
)
from phoenixguard.study.probability_calibration_v3 import (
    ProbabilityCalibratorV3,
    calibration_metrics_v3,
    fit_calibrators_v3,
    select_calibrator_v3,
    select_confidence_threshold_v3,
)


OPTIMIZED_MODEL_SCHEMA_VERSION = "PG_OPTIMIZED_HIDDEN_STATE_MODEL_V3"
OPTIMIZED_DATASET_SCHEMA_VERSION = "PG_OPTIMIZED_EVENT_DATASET_V3"
DEFAULT_OPTIMIZED_MODEL_NAME = "PG_OPTIMIZED_HIDDEN_STATE_MODEL_V3.json.gz"
BASE_MODEL_NAMES: tuple[str, ...] = (
    "empirical_prior",
    "gradient_boosted_event",
    "sequence_gru",
    "patch_sequence_transformer",
    "prefix_geometry_fusion",
)


def _safe_probability(value: Any, default: float = 0.5) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(parsed):
        return float(default)
    return float(np.clip(parsed, 1e-6, 1.0 - 1e-6))


class ConstantProbabilityModelV3:
    def __init__(self, probability: float) -> None:
        self.probability = _safe_probability(probability)

    def predict_proba(self, values: NDArray[Any]) -> NDArray[Any]:
        count = int(len(values))
        positive = np.full(count, self.probability, dtype=np.float64)
        return np.column_stack((1.0 - positive, positive))


class EmpiricalEventPriorV3:
    def __init__(self, alpha: float = 6.0) -> None:
        self.alpha = float(alpha)
        self.global_probability = 0.5
        self.tables: list[dict[tuple[str, ...], tuple[int, int]]] = []

    @staticmethod
    def _keys(event: Mapping[str, Any]) -> list[tuple[str, ...]]:
        event_type = str(event.get("event_type") or "NO_OPPORTUNITY")
        side = str(event.get("side_candidate") or "HOLD")
        timeframe = str(event.get("timeframe") or "UNKNOWN")
        symbol = str(event.get("symbol") or "UNKNOWN")
        return [
            (event_type, side, symbol, timeframe),
            (event_type, side, timeframe),
            (event_type, side),
            (event_type,),
        ]

    def fit(
        self,
        events: Sequence[Mapping[str, Any]],
        labels: NDArray[Any],
    ) -> "EmpiricalEventPriorV3":
        self.global_probability = float(np.mean(labels)) if labels.size else 0.5
        mutable: list[dict[tuple[str, ...], list[int]]] = [dict() for _ in range(4)]
        for event, label in zip(events, labels, strict=True):
            for depth, key in enumerate(self._keys(event)):
                counts = mutable[depth].setdefault(key, [0, 0])
                counts[0] += int(label)
                counts[1] += 1
        self.tables = [
            {key: (values[0], values[1]) for key, values in table.items()}
            for table in mutable
        ]
        return self

    def predict(self, events: Sequence[Mapping[str, Any]]) -> NDArray[Any]:
        output: list[float] = []
        for event in events:
            probability = self.global_probability
            for depth, key in enumerate(self._keys(event)):
                if depth >= len(self.tables):
                    continue
                counts = self.tables[depth].get(key)
                if not counts:
                    continue
                positive, total = counts
                probability = (
                    positive + self.alpha * self.global_probability
                ) / (total + self.alpha)
                if total >= 4:
                    break
            output.append(_safe_probability(probability))
        return np.asarray(output, dtype=np.float64)


_torch_available: bool
if TYPE_CHECKING:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset

    _torch_available = True
else:
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset

        _torch_available = True
    except Exception:
        torch = cast(Any, None)
        nn = cast(Any, None)
        DataLoader = cast(Any, None)
        TensorDataset = cast(Any, None)
        _torch_available = False


if TYPE_CHECKING or _torch_available:
    class _SequenceGRUV3(nn.Module):
        def __init__(self, channels: int = 6, hidden: int = 24) -> None:
            super_object = cast(Any, super())
            super_object.__init__()
            nn_api = cast(Any, nn)
            self.gru = nn_api.GRU(channels, hidden, batch_first=True)
            self.output = nn_api.Sequential(
                nn_api.LayerNorm(hidden),
                nn_api.Linear(hidden, 1),
            )

        def forward(self, values: Any) -> Any:
            encoded, _ = self.gru(values)
            return self.output(encoded[:, -1, :]).squeeze(-1)


    class _PatchSequenceTransformerV3(nn.Module):
        def __init__(
            self,
            *,
            sequence_length: int = 64,
            channels: int = 6,
            patch_size: int = 8,
            width: int = 32,
        ) -> None:
            super_object = cast(Any, super())
            super_object.__init__()
            nn_api = cast(Any, nn)
            torch_api = cast(Any, torch)
            self.patch_size = int(patch_size)
            patch_count = max(1, sequence_length // patch_size)
            self.embedding = nn_api.Linear(channels * patch_size, width)
            self.position = nn_api.Parameter(torch_api.zeros(1, patch_count, width))
            layer = nn_api.TransformerEncoderLayer(
                d_model=width,
                nhead=4,
                dim_feedforward=64,
                dropout=0.05,
                batch_first=True,
                norm_first=True,
            )
            self.encoder = nn_api.TransformerEncoder(layer, num_layers=1)
            self.output = nn_api.Sequential(
                nn_api.LayerNorm(width),
                nn_api.Linear(width, 1),
            )

        def forward(self, values: Any) -> Any:
            batch, length, channels = values.shape
            usable = (length // self.patch_size) * self.patch_size
            values = values[:, -usable:, :]
            patches = values.reshape(
                batch,
                usable // self.patch_size,
                self.patch_size * channels,
            )
            encoded = self.embedding(patches)
            encoded = encoded + self.position[:, : encoded.shape[1], :]
            return self.output(self.encoder(encoded).mean(dim=1)).squeeze(-1)


def _fit_binary_classifier(
    model: Any,
    values: NDArray[Any],
    labels: NDArray[Any],
) -> Any:
    if labels.size == 0:
        return ConstantProbabilityModelV3(0.5)
    if np.unique(labels).size < 2:
        return ConstantProbabilityModelV3(float(labels[0]))
    model.fit(values, labels)
    return model


def _predict_probability(model: Any, values: NDArray[Any]) -> NDArray[Any]:
    probabilities = np.asarray(model.predict_proba(values), dtype=np.float64)
    if probabilities.ndim == 1:
        return np.clip(probabilities, 1e-6, 1.0 - 1e-6)
    return np.clip(probabilities[:, -1], 1e-6, 1.0 - 1e-6)


def _train_torch_model(
    kind: str,
    sequences: NDArray[Any],
    labels: NDArray[Any],
    *,
    epochs: int,
    seed: int,
    maximum_rows: int = 18000,
) -> Any:
    if not _torch_available:
        return ConstantProbabilityModelV3(float(np.mean(labels)) if labels.size else 0.5)
    if labels.size == 0 or np.unique(labels).size < 2:
        return ConstantProbabilityModelV3(float(np.mean(labels)) if labels.size else 0.5)
    torch_api = cast(Any, torch)
    nn_api = cast(Any, nn)
    data_loader_cls = cast(Any, DataLoader)
    tensor_dataset_cls = cast(Any, TensorDataset)
    torch_api.manual_seed(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch_api.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    if len(labels) > maximum_rows:
        rng = np.random.default_rng(seed)
        indices = np.sort(rng.choice(len(labels), size=maximum_rows, replace=False))
        sequences = sequences[indices]
        labels = labels[indices]
    model = _SequenceGRUV3() if kind == "gru" else _PatchSequenceTransformerV3()
    model.train()
    positive = max(1.0, float(labels.sum()))
    negative = max(1.0, float(len(labels) - labels.sum()))
    criterion = nn_api.BCEWithLogitsLoss(
        pos_weight=torch_api.tensor(
            [negative / positive],
            dtype=torch_api.float32,
        )
    )
    optimizer = torch_api.optim.AdamW(
        model.parameters(),
        lr=0.002,
        weight_decay=0.001,
    )
    dataset = tensor_dataset_cls(
        torch_api.from_numpy(sequences.astype(np.float32, copy=False)),
        torch_api.from_numpy(labels.astype(np.float32, copy=False)),
    )
    generator = torch_api.Generator().manual_seed(seed)
    loader = data_loader_cls(
        dataset,
        batch_size=256,
        shuffle=True,
        generator=generator,
    )
    for _ in range(max(1, int(epochs))):
        for batch_values, batch_labels in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch_values), batch_labels)
            loss.backward()
            nn_api.utils.clip_grad_norm_(model.parameters(), 2.0)
            optimizer.step()
    model.eval()
    return model


def _predict_torch(model: Any, sequences: NDArray[Any]) -> NDArray[Any]:
    if isinstance(model, ConstantProbabilityModelV3):
        return _predict_probability(model, sequences)
    if not _torch_available:
        return np.full(len(sequences), 0.5, dtype=np.float64)
    torch_api = cast(Any, torch)
    output: list[NDArray[Any]] = []
    model.eval()
    with torch_api.no_grad():
        for start in range(0, len(sequences), 512):
            batch = torch_api.from_numpy(
                sequences[start : start + 512].astype(np.float32, copy=False)
            )
            output.append(torch_api.sigmoid(model(batch)).cpu().numpy())
    return np.clip(np.concatenate(output), 1e-6, 1.0 - 1e-6)


def _fusion_features(
    tabular: NDArray[Any],
    sequences: NDArray[Any],
) -> NDArray[Any]:
    means = sequences.mean(axis=1)
    standard = sequences.std(axis=1)
    maxima = sequences.max(axis=1)
    minima = sequences.min(axis=1)
    patch_means = sequences.reshape(len(sequences), 8, 8, 6).mean(axis=2).reshape(len(sequences), -1)
    return np.concatenate((tabular, means, standard, maxima, minima, patch_means), axis=1)


@dataclass
class OptimizedDatasetV3:
    rows: list[dict[str, Any]]
    events: list[dict[str, Any]]
    targets: list[dict[str, Any]]
    features: NDArray[Any]
    sequences: NDArray[Any]
    labels: NDArray[Any]
    direction_labels: NDArray[Any]
    groups: NDArray[Any]
    folds: NDArray[Any]
    all_window_count: int
    eligible_window_count: int
    family_count: int
    leakage_audit: dict[str, Any]


@dataclass
class OptimizedModelBundleV3:
    prior: Any
    boosted: Any
    sequence_gru: Any
    patch_transformer: Any
    fusion: Any
    meta_labeler: Any
    calibrator: ProbabilityCalibratorV3
    threshold: float


def load_cached_sequences_v3(cache_path: str | Path) -> list[ExtractedSequenceV3]:
    latest_by_path: dict[str, ExtractedSequenceV3] = {}
    with Path(cache_path).open("r", encoding="utf-8") as handle:
        for line in handle:
            raw: dict[str, Any] = json.loads(line)
            allowed = {
                key: value
                for key, value in raw.items()
                if key in ExtractedSequenceV3.__dataclass_fields__
            }
            record = ExtractedSequenceV3(**allowed)
            path_key = os.path.normcase(os.path.abspath(record.path))
            latest_by_path[path_key] = record
    return [
        latest_by_path[key]
        for key in sorted(latest_by_path)
    ]


def build_optimized_dataset_v3(
    records: Sequence[ExtractedSequenceV3],
    *,
    folds: int = 5,
    horizons: Sequence[int] = DEFAULT_OPTIMIZED_HORIZONS,
    minimum_prefix: int = 24,
    stride: int = 1,
    trade_horizon: int = 21,
) -> OptimizedDatasetV3:
    families = group_sequence_families_v3(records)
    record_folds = assign_grouped_folds_v3(families, folds=folds)
    fold_by_family = {
        family: int(record_folds[index])
        for index, family in enumerate(families)
    }
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    features: list[NDArray[Any]] = []
    sequences: list[NDArray[Any]] = []
    labels: list[int] = []
    direction_labels: list[int] = []
    groups: list[str] = []
    fold_rows: list[int] = []
    audit_rows: list[dict[str, Any]] = []
    all_windows = 0
    required_suffix = max(int(trade_horizon), max(int(value) for value in horizons))
    for record_index, record in enumerate(records):
        if record.extraction_status != "EXTRACTED":
            continue
        family_id = families[record_index]
        upper = len(record.candles) - required_suffix
        for cutoff in range(int(minimum_prefix), upper + 1, max(1, int(stride))):
            all_windows += 1
            event = build_event_window_v3(
                record.candles,
                cutoff=cutoff,
                image_hash=record.image_hash,
                family_id=family_id,
                symbol=record.symbol,
                timeframe=record.timeframe,
                path=record.path,
            )
            audit_rows.append({"event": event})
            if not event["eligible"]:
                continue
            target = build_optimized_targets_v3(
                record.candles,
                cutoff=cutoff,
                side_candidate=str(event["side_candidate"]),
                visible_maturity=str(event["visible_maturity"]),
                horizons=horizons,
                trade_horizon=trade_horizon,
            )
            trade_path = dict(target["trade_path"])
            row = {
                "event": event,
                "target": target,
                "fold": int(record_folds[record_index]),
            }
            rows.append(row)
            events.append(event)
            targets.append(target)
            features.append(feature_vector_v3(event))
            sequences.append(
                sequence_tensor_v3(record.candles, cutoff=cutoff, length=64)
            )
            labels.append(int(bool(trade_path["target_before_invalidation"])))
            direction_labels.append(int(bool(target["candidate_direction_correct"])))
            groups.append(family_id)
            fold_rows.append(int(record_folds[record_index]))
    audit = audit_optimized_windows_v3(
        audit_rows,
        fold_by_family=fold_by_family,
    )
    assert_leakage_audit_v3(audit)
    feature_matrix = (
        np.stack(features).astype(np.float32)
        if features
        else np.zeros((0, len(FEATURE_NAMES)), dtype=np.float32)
    )
    sequence_matrix = (
        np.stack(sequences).astype(np.float32)
        if sequences
        else np.zeros((0, 64, 6), dtype=np.float32)
    )
    return OptimizedDatasetV3(
        rows=rows,
        events=events,
        targets=targets,
        features=feature_matrix,
        sequences=sequence_matrix,
        labels=np.asarray(labels, dtype=np.int64),
        direction_labels=np.asarray(direction_labels, dtype=np.int64),
        groups=np.asarray(groups, dtype=object),
        folds=np.asarray(fold_rows, dtype=np.int64),
        all_window_count=all_windows,
        eligible_window_count=len(rows),
        family_count=len(set(families)),
        leakage_audit=audit,
    )


def _partition_groups(
    groups: NDArray[Any],
) -> tuple[NDArray[Any], NDArray[Any], NDArray[Any]]:
    unique = sorted(str(value) for value in np.unique(groups))
    if len(unique) < 5:
        indices = np.arange(len(groups))
        return indices, indices[:0], indices[:0]
    meta_groups = set(unique[::5])
    calibration_groups = set(unique[1::5])
    fit = np.asarray(
        [index for index, value in enumerate(groups) if str(value) not in meta_groups | calibration_groups],
        dtype=np.int64,
    )
    meta = np.asarray(
        [index for index, value in enumerate(groups) if str(value) in meta_groups],
        dtype=np.int64,
    )
    calibration = np.asarray(
        [index for index, value in enumerate(groups) if str(value) in calibration_groups],
        dtype=np.int64,
    )
    return fit, meta, calibration


def _fit_base_models(
    events: Sequence[Mapping[str, Any]],
    features: NDArray[Any],
    sequences: NDArray[Any],
    labels: NDArray[Any],
    *,
    neural_epochs: int,
    seed: int,
) -> dict[str, Any]:
    prior = EmpiricalEventPriorV3().fit(events, labels)
    boosted = _fit_binary_classifier(
        HistGradientBoostingClassifier(
            learning_rate=0.055,
            max_iter=90,
            max_leaf_nodes=15,
            min_samples_leaf=24,
            l2_regularization=1.5,
            random_state=seed,
        ),
        features,
        labels,
    )
    fusion = _fit_binary_classifier(
        ExtraTreesClassifier(
            n_estimators=96,
            max_depth=10,
            min_samples_leaf=12,
            max_features=0.7,
            class_weight="balanced",
            n_jobs=1,
            random_state=seed,
        ),
        _fusion_features(features, sequences),
        labels,
    )
    return {
        "prior": prior,
        "boosted": boosted,
        "sequence_gru": _train_torch_model(
            "gru",
            sequences,
            labels,
            epochs=neural_epochs,
            seed=seed + 11,
        ),
        "patch_transformer": _train_torch_model(
            "patch",
            sequences,
            labels,
            epochs=neural_epochs,
            seed=seed + 23,
        ),
        "fusion": fusion,
    }


def _base_probabilities(
    models: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    features: NDArray[Any],
    sequences: NDArray[Any],
) -> NDArray[Any]:
    columns = [
        models["prior"].predict(events),
        _predict_probability(models["boosted"], features),
        _predict_torch(models["sequence_gru"], sequences),
        _predict_torch(models["patch_transformer"], sequences),
        _predict_probability(models["fusion"], _fusion_features(features, sequences)),
    ]
    return np.column_stack(columns).astype(np.float64)


def _fit_meta_labeler(values: NDArray[Any], labels: NDArray[Any]) -> Any:
    return _fit_binary_classifier(
        LogisticRegression(
            C=0.35,
            class_weight="balanced",
            max_iter=1000,
            random_state=37,
        ),
        values,
        labels,
    )


def _fit_bundle(
    dataset: OptimizedDatasetV3,
    indices: NDArray[Any],
    *,
    neural_epochs: int,
    seed: int,
) -> tuple[OptimizedModelBundleV3, dict[str, Any]]:
    local_groups = dataset.groups[indices]
    fit_local, meta_local, calibration_local = _partition_groups(local_groups)
    fit_indices = indices[fit_local]
    meta_indices = indices[meta_local]
    calibration_indices = indices[calibration_local]
    if not len(meta_indices):
        meta_indices = fit_indices
    if not len(calibration_indices):
        calibration_indices = meta_indices
    models = _fit_base_models(
        [dataset.events[int(index)] for index in fit_indices],
        dataset.features[fit_indices],
        dataset.sequences[fit_indices],
        dataset.labels[fit_indices],
        neural_epochs=neural_epochs,
        seed=seed,
    )
    meta_values = _base_probabilities(
        models,
        [dataset.events[int(index)] for index in meta_indices],
        dataset.features[meta_indices],
        dataset.sequences[meta_indices],
    )
    meta = _fit_meta_labeler(meta_values, dataset.labels[meta_indices])
    calibration_values = _base_probabilities(
        models,
        [dataset.events[int(index)] for index in calibration_indices],
        dataset.features[calibration_indices],
        dataset.sequences[calibration_indices],
    )
    raw_calibration = _predict_probability(meta, calibration_values)
    calibrators = fit_calibrators_v3(
        dataset.labels[calibration_indices],
        raw_calibration,
    )
    calibrator, calibration_report = select_calibrator_v3(
        calibrators,
        dataset.labels[calibration_indices],
        raw_calibration,
    )
    calibrated = calibrator.predict(raw_calibration)
    threshold = select_confidence_threshold_v3(
        dataset.labels[calibration_indices],
        calibrated,
    )
    bundle = OptimizedModelBundleV3(
        prior=models["prior"],
        boosted=models["boosted"],
        sequence_gru=models["sequence_gru"],
        patch_transformer=models["patch_transformer"],
        fusion=models["fusion"],
        meta_labeler=meta,
        calibrator=calibrator,
        threshold=float(threshold["threshold"]),
    )
    return bundle, {
        "fit_rows": int(len(fit_indices)),
        "meta_rows": int(len(meta_indices)),
        "calibration_rows": int(len(calibration_indices)),
        "fit_families": int(len(np.unique(dataset.groups[fit_indices]))),
        "meta_families": int(len(np.unique(dataset.groups[meta_indices]))),
        "calibration_families": int(len(np.unique(dataset.groups[calibration_indices]))),
        "calibration": calibration_report,
        "threshold": threshold,
        "meta_event_ids": [dataset.events[index]["event_id"] for index in meta_indices],
        "calibration_event_ids": [dataset.events[index]["event_id"] for index in calibration_indices],
    }


def _bundle_probabilities(
    bundle: OptimizedModelBundleV3,
    events: Sequence[Mapping[str, Any]],
    features: NDArray[Any],
    sequences: NDArray[Any],
) -> NDArray[Any]:
    models = {
        "prior": bundle.prior,
        "boosted": bundle.boosted,
        "sequence_gru": bundle.sequence_gru,
        "patch_transformer": bundle.patch_transformer,
        "fusion": bundle.fusion,
    }
    base = _base_probabilities(models, events, features, sequences)
    raw = _predict_probability(bundle.meta_labeler, base)
    return bundle.calibrator.predict(raw)


def _breakdown(
    predictions: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in predictions:
        grouped.setdefault(str(row.get(key) or "UNKNOWN"), []).append(row)
    output: dict[str, Any] = {}
    for name, rows in sorted(grouped.items()):
        selected = [row for row in rows if row["selected_high_confidence"]]
        output[name] = {
            "rows": len(rows),
            "direction_accuracy": round(
                sum(bool(row["direction_correct"]) for row in rows) / max(1, len(rows)),
                6,
            ),
            "selected": len(selected),
            "selected_precision": round(
                sum(bool(row["target_before_invalidation"]) for row in selected)
                / max(1, len(selected)),
                6,
            ),
        }
    return output


def summarize_optimized_predictions_v3(
    predictions: Sequence[Mapping[str, Any]],
    *,
    leakage_audit: Mapping[str, Any],
    old_cross_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    y = np.asarray(
        [int(bool(row["target_before_invalidation"])) for row in predictions],
        dtype=np.int64,
    )
    probabilities = np.asarray(
        [float(row["probability"]) for row in predictions],
        dtype=np.float64,
    )
    selected = np.asarray(
        [bool(row["selected_high_confidence"]) for row in predictions],
        dtype=bool,
    )
    positive = probabilities >= 0.5
    selected_count = int(selected.sum())
    selected_precision = float(y[selected].mean()) if selected_count else 0.0
    coverage = selected_count / max(1, len(predictions))
    positive_precision = float(y[positive].mean()) if int(positive.sum()) else 0.0
    direction_accuracy = (
        sum(bool(row["direction_correct"]) for row in predictions)
        / max(1, len(predictions))
    )
    pullbacks = [row for row in predictions if row["event_type"] == "PULLBACK_VISIBLE"]
    pullback_accuracy = (
        sum(bool(row["direction_correct"]) for row in pullbacks)
        / max(1, len(pullbacks))
    )
    continuation = [
        row for row in predictions if row["event_type"] == "CONTINUATION_PRESSURE"
    ]
    counter_move_accuracy = (
        sum(
            (float(row["probability"]) < 0.5) == (not bool(row["direction_correct"]))
            for row in continuation
        )
        / max(1, len(continuation))
    )
    calibration = calibration_metrics_v3(y, probabilities)
    prevalence = float(y.mean()) if y.size else 0.0
    baseline_brier = prevalence * (1.0 - prevalence)
    old = dict(old_cross_validation or {})
    old_promotion = dict(old.get("promotion") or {})
    old_baseline_beaten = bool(
        float(old_promotion.get("primary_accuracy") or 0.0)
        > float(old_promotion.get("primary_baseline_accuracy") or 0.0)
    )
    gates = {
        "leakage_audit_pass": str(leakage_audit.get("status") or "") == "PASS",
        "grouped_validation_pass": True,
        "old_baseline_beaten": old_baseline_beaten,
        "visible_pullback_improves": pullback_accuracy > 0.576399,
        "high_confidence_precision_at_least_70": selected_precision >= 0.70,
        "coverage_at_least_20": coverage >= 0.20,
        "target_before_invalidation_precision_at_least_65": positive_precision >= 0.65,
        "brier_improves_over_prevalence": float(calibration["brier"]) < baseline_brier,
        "no_direct_execution_authority": True,
    }
    eligible = all(gates.values())
    return {
        "schema_version": OPTIMIZED_MODEL_SCHEMA_VERSION,
        "rows": len(predictions),
        "event_conditioned_direction_accuracy": round(direction_accuracy, 6),
        "target_before_invalidation_precision": round(positive_precision, 6),
        "high_confidence_selective_precision": round(selected_precision, 6),
        "high_confidence_coverage": round(coverage, 6),
        "selected_rows": selected_count,
        "visible_pullback_accuracy": round(pullback_accuracy, 6),
        "visible_pullback_rows": len(pullbacks),
        "future_counter_move_accuracy": round(counter_move_accuracy, 6),
        "future_counter_move_rows": len(continuation),
        "calibration": calibration,
        "prevalence_baseline_brier": round(baseline_brier, 6),
        "by_event": _breakdown(predictions, "event_type"),
        "by_pair": _breakdown(predictions, "symbol"),
        "by_timeframe": _breakdown(predictions, "timeframe"),
        "promotion": {
            "eligible": eligible,
            "reason": (
                "SELECTIVE_EVENT_PRECISION_AND_CALIBRATION_PROVEN"
                if eligible
                else "ROOT_CAUSE_BLOCKED_BY_DATA_OR_LABEL_QUALITY"
            ),
            "gates": gates,
        },
    }


def cross_validate_optimized_hidden_state_v3(
    dataset: OptimizedDatasetV3,
    *,
    folds: int = 5,
    neural_epochs: int = 1,
    minimum_free_gb: float = 45.0,
    reserve_path: str | Path = ".",
    old_cross_validation: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    predictions: list[dict[str, Any]] = []
    fold_reports: list[dict[str, Any]] = []
    fold_calibration_test_disjoint = True
    for fold in range(int(folds)):
        enforce_disk_reserve(reserve_path, minimum_free_gb=minimum_free_gb)
        test_indices = np.flatnonzero(dataset.folds == fold)
        train_indices = np.flatnonzero(dataset.folds != fold)
        if not len(test_indices) or not len(train_indices):
            continue
        bundle, training_report = _fit_bundle(
            dataset,
            train_indices,
            neural_epochs=neural_epochs,
            seed=1700 + fold,
        )
        probabilities = _bundle_probabilities(
            bundle,
            [dataset.events[int(index)] for index in test_indices],
            dataset.features[test_indices],
            dataset.sequences[test_indices],
        )
        fold_rows: list[dict[str, Any]] = []
        for local_index, row_index in enumerate(test_indices):
            event = dataset.events[int(row_index)]
            target = dataset.targets[int(row_index)]
            path: dict[str, Any] = dict(target["trade_path"])
            probability = float(probabilities[local_index])
            prediction: dict[str, Any] = {
                "event_id": event["event_id"],
                "image_hash": event["image_hash"],
                "family_id": event["family_id"],
                "fold": fold,
                "cutoff": event["cutoff"],
                "symbol": event["symbol"],
                "timeframe": event["timeframe"],
                "event_type": event["event_type"],
                "side_candidate": event["side_candidate"],
                "visible_maturity": event["visible_maturity"],
                "probability": round(probability, 8),
                "threshold": round(bundle.threshold, 8),
                "selected_high_confidence": probability >= bundle.threshold,
                "target_before_invalidation": bool(path["target_before_invalidation"]),
                "outcome": path["outcome"],
                "mfe_ranges": path["mfe_ranges"],
                "mae_ranges": path["mae_ranges"],
                "drawdown_first": path["drawdown_first"],
                "direction_correct": bool(target["candidate_direction_correct"]),
                "future_suffix_used_by_scorer_only": True,
                "folder_label_used_as_target": False,
            }
            predictions.append(prediction)
            fold_rows.append(prediction)
        fold_calibration_ids = set(
            cast(Sequence[str], training_report.pop("calibration_event_ids"))
        )
        training_report.pop("meta_event_ids", None)
        fold_test_ids = {
            str(dataset.events[int(index)]["event_id"]) for index in test_indices
        }
        fold_calibration_test_disjoint = bool(
            fold_calibration_test_disjoint
            and not fold_calibration_ids.intersection(fold_test_ids)
        )
        fold_reports.append(
            {
                "fold": fold,
                "test_rows": len(test_indices),
                "test_families": int(len(np.unique(dataset.groups[test_indices]))),
                "metrics": summarize_optimized_predictions_v3(
                    fold_rows,
                    leakage_audit=dataset.leakage_audit,
                    old_cross_validation=old_cross_validation,
                ),
                "training": training_report,
            }
        )
    leakage = audit_optimized_windows_v3(
        dataset.rows,
        fold_by_family={
            str(group): int(dataset.folds[index])
            for index, group in enumerate(dataset.groups)
        },
        calibration_event_ids=(),
        test_event_ids=(),
    )
    leakage["checks"]["calibration_and_test_events_disjoint"] = (
        fold_calibration_test_disjoint
    )
    leakage["status"] = (
        "PASS"
        if all(bool(value) for value in leakage["checks"].values())
        else "FAIL"
    )
    leakage["failures"] = [
        key for key, value in leakage["checks"].items() if not value
    ]
    assert_leakage_audit_v3(leakage)
    summary = summarize_optimized_predictions_v3(
        predictions,
        leakage_audit=leakage,
        old_cross_validation=old_cross_validation,
    )
    summary["folds"] = fold_reports
    summary["leakage_audit"] = leakage
    summary["model_suite"] = list(BASE_MODEL_NAMES) + ["calibrated_meta_labeler"]
    summary["independent_family_count"] = dataset.family_count
    summary["all_window_count"] = dataset.all_window_count
    summary["eligible_event_window_count"] = dataset.eligible_window_count
    return summary, predictions


def train_production_bundle_v3(
    dataset: OptimizedDatasetV3,
    *,
    neural_epochs: int = 1,
) -> tuple[OptimizedModelBundleV3, dict[str, Any]]:
    indices = np.arange(len(dataset.labels), dtype=np.int64)
    return _fit_bundle(
        dataset,
        indices,
        neural_epochs=neural_epochs,
        seed=8083,
    )


def save_optimized_model_v3(
    bundle: OptimizedModelBundleV3,
    *,
    summary: Mapping[str, Any],
    training_report: Mapping[str, Any],
    path: str | Path,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    blob = base64.b64encode(
        pickle.dumps(bundle, protocol=pickle.HIGHEST_PROTOCOL)
    ).decode("ascii")
    artifact = {
        "schema_version": OPTIMIZED_MODEL_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "model_suite": list(BASE_MODEL_NAMES) + ["calibrated_meta_labeler"],
        "promotion": dict(summary.get("promotion") or {}),
        "metrics": dict(summary),
        "production_training": dict(training_report),
        "model_blob_base64": blob,
        "study_only": True,
        "execution_authority": "NONE",
        "grants_entry_permission": False,
    }
    with gzip.open(destination, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(artifact, handle, separators=(",", ":"), ensure_ascii=True)
    return destination


class OptimizedHiddenStateModelV3:
    def __init__(self, artifact: Mapping[str, Any], *, source_path: str | Path) -> None:
        self.artifact = dict(artifact)
        self.source_path = str(source_path)
        blob = base64.b64decode(str(self.artifact["model_blob_base64"]).encode("ascii"))
        self.bundle: OptimizedModelBundleV3 = pickle.loads(blob)

    @classmethod
    def load(cls, path: str | Path) -> "OptimizedHiddenStateModelV3":
        with gzip.open(Path(path), "rt", encoding="utf-8") as handle:
            artifact: dict[str, Any] = json.load(handle)
        if artifact.get("schema_version") != OPTIMIZED_MODEL_SCHEMA_VERSION:
            raise ValueError("PG_OPTIMIZED_MODEL_SCHEMA_MISMATCH")
        return cls(artifact, source_path=path)

    def predict(
        self,
        *,
        candles: Sequence[Mapping[str, Any]],
        symbol: object,
        timeframe: object,
    ) -> dict[str, Any]:
        event = build_event_window_v3(
            candles,
            cutoff=len(candles),
            image_hash="LIVE_PREFIX",
            family_id="LIVE",
            symbol=symbol,
            timeframe=timeframe,
        )
        promotion = dict(self.artifact.get("promotion") or {})
        if not event["eligible"]:
            return {
                "schema_version": OPTIMIZED_MODEL_SCHEMA_VERSION,
                "status": "OBSERVING",
                "side": "HOLD",
                "event_type": event["event_type"],
                "opportunity_maturity": event["visible_maturity"],
                "target_before_invalidation_probability": 0.0,
                "selected_high_confidence": False,
                "calibrated": True,
                "promotion_eligible": bool(promotion.get("eligible", False)),
                "study_only": True,
                "execution_authority": "NONE",
                "grants_entry_permission": False,
            }
        features = feature_vector_v3(event).reshape(1, -1)
        sequences = sequence_tensor_v3(
            candles,
            cutoff=len(candles),
            length=64,
        ).reshape(1, 64, 6)
        probability = float(
            _bundle_probabilities(self.bundle, [event], features, sequences)[0]
        )
        selected = bool(probability >= self.bundle.threshold)
        return {
            "schema_version": OPTIMIZED_MODEL_SCHEMA_VERSION,
            "status": "ACTIVE" if promotion.get("eligible") else "DIAGNOSTIC",
            "side": event["side_candidate"],
            "event_type": event["event_type"],
            "opportunity_maturity": (
                "HIGH_CONFIDENCE_EVENT"
                if selected
                else event["visible_maturity"]
            ),
            "target_before_invalidation_probability": round(probability, 6),
            "selection_threshold": round(float(self.bundle.threshold), 6),
            "selected_high_confidence": selected,
            "calibrated": True,
            "promotion_eligible": bool(promotion.get("eligible", False)),
            "visible_prefix_hash": event["visible_prefix_hash"],
            "study_only": True,
            "execution_authority": "NONE",
            "grants_entry_permission": False,
        }


def resolve_optimized_model_path_v3(root_dir: str | Path | None = None) -> Path | None:
    configured = str(os.getenv("PHOENIXGUARD_OPTIMIZED_HIDDEN_STATE_MODEL", "") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    if root_dir is not None:
        root = Path(root_dir)
        candidates.extend(
            (
                root / "Backend" / "src" / "phoenixguard" / DEFAULT_OPTIMIZED_MODEL_NAME,
                root / DEFAULT_OPTIMIZED_MODEL_NAME,
            )
        )
    candidates.append(Path(__file__).resolve().parents[1] / DEFAULT_OPTIMIZED_MODEL_NAME)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def load_default_optimized_hidden_state_model_v3(
    root_dir: str | Path | None = None,
) -> OptimizedHiddenStateModelV3 | None:
    path = resolve_optimized_model_path_v3(root_dir)
    return OptimizedHiddenStateModelV3.load(path) if path is not None else None
