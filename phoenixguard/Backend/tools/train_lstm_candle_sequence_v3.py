from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import csv
import hashlib
import json
import math
import os
import random
import shutil
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
    ARTIFACT_BUNDLE_MANIFEST_SCHEMA,
    DEFAULT_CONFIG_PATH,
    DIRECT_RAW_CV_ARCHITECTURE,
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
    TRAJECTORY_MODE_LABELS,
    artifact_bundle_generation_root,
    artifact_bundle_manifest_path,
    causal_chart_context_tensor,
    candle_sequence_geometry_quality,
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
DEFAULT_SEQUENCE_CACHE = (
    PROJECT_ROOT / "data_splits" / "lstm_raw_candle_sequences_v3.jsonl"
)
DEFAULT_TRAINING_CHECKPOINT = (
    PROJECT_ROOT / "models" / "lstm_candle_sequence_v3_training.pt"
)
EXTRACTOR_SCHEMA_VERSION = (
    "PG_ADAPTIVE_PALETTE_OHLC_EXTRACTOR_V3_PAIR_COHERENCE_20260715"
)
CHART_CONTEXT_SIZE = (32, 64)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
SIDE_TO_INDEX = {"BUY": 0, "SELL": 1}
INDEX_TO_SIDE = {0: "BUY", 1: "SELL"}
PLAY_TO_INDEX = {label: index for index, label in enumerate(PLAY_LABELS)}
PATH_TARGET_FEATURE = "relative_price_delta_scaled"
PATH_TARGET_FEATURE_INDEX = FEATURE_SCHEMA.index(PATH_TARGET_FEATURE)
PATH_PREDICTION_INDEX = PREDICTION_SCHEMA.index(PATH_TARGET_FEATURE)
PATH_DIRECTION_LABELS = TRAJECTORY_MODE_LABELS
PATH_DIRECTION_HOLD_THRESHOLD_SCALED = 0.02


def _temporary_publish_path(path: Path) -> Path:
    return path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )


def _atomic_torch_save(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_publish_path(path)
    try:
        torch.save(payload, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_artifact_bundle(
    *,
    artifact: Mapping[str, Any],
    config: Mapping[str, Any],
    metrics: Mapping[str, Any],
    model_path: Path,
    config_path: Path,
    metrics_path: Path,
    _before_manifest_switch: Callable[[], None] | None = None,
) -> None:
    """Publish one immutable V3 generation through one atomic pointer switch.

    The generation directory is complete and hash-validated before the small
    manifest is atomically replaced.  An interruption before that replacement
    can leave only an unreachable generation directory; the prior pointer and
    its files remain usable.  The public trio is refreshed after the switch as
    a compatibility mirror, while manifest-aware runtime reads never depend on
    those three non-transactional paths.
    """

    artifact_payload = dict(artifact)
    config_payload = dict(config)
    metrics_payload = dict(metrics)
    generations = {
        str(artifact_payload.get("artifact_generation_id") or ""),
        str(config_payload.get("artifact_generation_id") or ""),
        str(metrics_payload.get("artifact_generation_id") or ""),
    }
    if len(generations) != 1 or not next(iter(generations)):
        raise RuntimeError("staged V3 artifact generation is inconsistent")
    generation_id = next(iter(generations))

    public_paths = (Path(model_path), Path(config_path), Path(metrics_path))
    if len({path.name for path in public_paths}) != 3:
        raise RuntimeError("V3 artifact public filenames must be distinct")
    manifest_path = artifact_bundle_manifest_path(public_paths[0])
    generation_root = artifact_bundle_generation_root(public_paths[0])
    generation_path = generation_root / generation_id
    staging_path = generation_root / (
        f".{generation_id}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    for path in (*public_paths, manifest_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    generation_root.mkdir(parents=True, exist_ok=True)
    staging_path.mkdir(parents=False, exist_ok=False)

    staged_model = staging_path / public_paths[0].name
    staged_config = staging_path / public_paths[1].name
    staged_metrics = staging_path / public_paths[2].name
    staged_paths = (staged_model, staged_config, staged_metrics)
    try:
        torch.save(artifact_payload, staged_model)
        staged_config.write_text(
            json.dumps(config_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        staged_metrics.write_text(
            json.dumps(metrics_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        reloaded_artifact = cast(
            Mapping[str, Any],
            torch.load(staged_model, map_location="cpu", weights_only=False),
        )
        reloaded_config = json.loads(staged_config.read_text(encoding="utf-8"))
        reloaded_metrics = json.loads(staged_metrics.read_text(encoding="utf-8"))
        reloaded_generations = {
            str(reloaded_artifact.get("artifact_generation_id") or ""),
            str(reloaded_config.get("artifact_generation_id") or ""),
            str(reloaded_metrics.get("artifact_generation_id") or ""),
        }
        if reloaded_generations != {generation_id}:
            raise RuntimeError("staged V3 artifact generation is inconsistent")

        staged_hashes = {
            path.name: _sha256_file(path) for path in staged_paths
        }
        if generation_path.exists():
            existing_paths = tuple(
                generation_path / public_path.name for public_path in public_paths
            )
            existing_hashes = {
                path.name: _sha256_file(path)
                for path in existing_paths
                if path.is_file()
            }
            if existing_hashes != staged_hashes:
                raise RuntimeError(
                    f"immutable V3 generation already exists with different bytes: {generation_id}"
                )
            shutil.rmtree(staging_path)
        else:
            staging_path.replace(generation_path)

        immutable_paths = tuple(
            generation_path / public_path.name for public_path in public_paths
        )
        roles = ("model", "config", "metrics")
        manifest_parent = manifest_path.parent.resolve()
        manifest = {
            "schema_version": ARTIFACT_BUNDLE_MANIFEST_SCHEMA,
            "artifact_generation_id": generation_id,
            "published_at_unix_ns": time.time_ns(),
            "public_paths": {
                role: Path(os.path.relpath(path.resolve(), manifest_parent)).as_posix()
                for role, path in zip(roles, public_paths)
            },
            "files": {
                role: {
                    "path": Path(
                        os.path.relpath(path.resolve(), manifest_parent)
                    ).as_posix(),
                    "sha256": _sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
                for role, path in zip(roles, immutable_paths)
            },
        }
        if _before_manifest_switch is not None:
            _before_manifest_switch()
        manifest_temporary = _temporary_publish_path(manifest_path)
        try:
            manifest_temporary.write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            reloaded_manifest = json.loads(
                manifest_temporary.read_text(encoding="utf-8")
            )
            if reloaded_manifest != manifest:
                raise RuntimeError("staged V3 artifact manifest failed validation")
            manifest_temporary.replace(manifest_path)
        finally:
            manifest_temporary.unlink(missing_ok=True)

        # Compatibility only.  Manifest-aware runtime has already switched
        # atomically and remains correct even if a legacy mirror write fails.
        mirror_pairs = (
            (immutable_paths[1], public_paths[1]),
            (immutable_paths[2], public_paths[2]),
            (immutable_paths[0], public_paths[0]),
        )
        for immutable_path, public_path in mirror_pairs:
            mirror_temporary = _temporary_publish_path(public_path)
            try:
                shutil.copyfile(immutable_path, mirror_temporary)
                if _sha256_file(mirror_temporary) != _sha256_file(immutable_path):
                    raise RuntimeError("V3 artifact compatibility mirror hash mismatch")
                mirror_temporary.replace(public_path)
            finally:
                mirror_temporary.unlink(missing_ok=True)
    finally:
        if staging_path.exists():
            shutil.rmtree(staging_path)
PATH_DIRECTION_LOGIT_SCALE = 12.0
PATHWISE_CONFORMAL_ALPHA = 0.10
PATHWISE_CONFORMAL_SCALE_FLOOR = 1e-4
TRAINING_TARGET_SCHEMA_VERSION = "PG_DIRECT_CUMULATIVE_CLOSE_DISPLACEMENT_TARGET_V3"
TRAINING_RECIPE_SCHEMA_VERSION = (
    "PG_PATH_FIRST_RAW_PIXEL_PYRAMID_LSTM64_REGIME_DECODER_RECIPE_V3"
)


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
    selected_cluster = _mapping(
        risk_control.get("test_selected_source_cluster_accuracy_95")
    )
    direction_cluster = _mapping(test_metrics.get("source_cluster_accuracy_95"))

    confusion = test_metrics.get("confusion_matrix")
    recalls: dict[str, float] = {"BUY": 0.0, "SELL": 0.0}
    if isinstance(confusion, Sequence) and not isinstance(
        confusion, (str, bytes, bytearray)
    ):
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
    persistence_balanced = _finite_float(
        test_metrics.get("persistence_baseline_balanced_accuracy")
    )
    endpoint_accuracy = _finite_float(
        test_metrics.get("endpoint_path_direction_accuracy")
    )
    endpoint_balanced = _finite_float(
        test_metrics.get("endpoint_path_balanced_accuracy")
    )
    endpoint_persistence = _finite_float(
        test_metrics.get("endpoint_path_persistence_accuracy")
    )
    event_balanced = _finite_float(test_metrics.get("path_movement_balanced_accuracy"))
    position_balanced = _finite_float(
        test_metrics.get("horizon_position_balanced_accuracy")
    )
    endpoint_cluster = _mapping(test_metrics.get("endpoint_source_cluster_accuracy_95"))
    endpoint_support = _mapping(test_metrics.get("endpoint_predicted_support"))
    pathwise = _mapping(test_metrics.get("pathwise_conformal"))
    test_windows = max(
        1,
        sum(int(endpoint_support.get(side, 0) or 0) for side in PATH_DIRECTION_LABELS),
    )
    minimum_endpoint_side_support = max(10, int(math.ceil(0.05 * test_windows)))
    minimum_selected_sources = 10
    path_checks = {
        "independent_split_support": (
            int(source_counts.get("train", 0) or 0) >= 100
            and int(source_counts.get("validation", 0) or 0) >= 20
            and int(source_counts.get("test", 0) or 0) >= 20
        ),
        # Three-way BUY/SELL/HOLD chance performance is ~0.333.  A release
        # needs a material margin above that, not a rounding-error win.
        "path_movement_balanced_accuracy_at_least_40": event_balanced >= 0.40,
        "horizon_position_balanced_accuracy_at_least_45": position_balanced >= 0.45,
        "endpoint_path_accuracy_at_least_55": endpoint_accuracy >= 0.55,
        "endpoint_path_balanced_accuracy_at_least_45": endpoint_balanced >= 0.45,
        "endpoint_beats_path_persistence_by_two_points": endpoint_accuracy
        >= endpoint_persistence + 0.02,
        "endpoint_source_cluster_lower_95_above_chance": _finite_float(
            endpoint_cluster.get("lower_95")
        )
        > 0.50,
        "endpoint_predictions_include_buy_and_sell": all(
            int(endpoint_support.get(side, 0) or 0) >= minimum_endpoint_side_support
            for side in ("BUY", "SELL")
        ),
        "path_delta_mae_at_most_08": _finite_float(
            test_metrics.get("path_delta_mae"), 1.0
        )
        <= 0.08,
        "pathwise_source_coverage_between_80_and_98": 0.80
        <= _finite_float(pathwise.get("source_simultaneous_coverage"))
        <= 0.98,
        "pathwise_mean_full_band_width_at_most_30": _finite_float(
            pathwise.get("mean_full_band_width_relative_price"),
            1.0,
        )
        <= 0.30,
    }
    auxiliary_checks = {
        "body_direction_balanced_accuracy_at_least_52": balanced >= 0.52,
        "body_direction_beats_persistence_by_one_point": balanced
        >= persistence_balanced + 0.01,
        "body_direction_source_cluster_lower_95_above_chance": _finite_float(
            direction_cluster.get("lower_95")
        )
        > 0.50,
        "both_body_direction_class_recalls_at_least_chance": all(
            value >= 0.50 for value in recalls.values()
        ),
        "locked_selective_accuracy_at_target": _finite_float(
            test_selection.get("accuracy")
        )
        >= required_precision,
        "locked_selective_macro_precision_at_target": _finite_float(
            test_selection.get("macro_predicted_class_precision")
        )
        >= required_precision,
        "locked_selective_overall_wilson_lower_at_target": _finite_float(
            test_selection.get("wilson_lower_95")
        )
        >= required_precision,
        "locked_selective_each_class_has_minimum_support": all(
            int(row.get("selected", 0) or 0) >= required_per_class
            for row in (buy, sell)
        ),
        "locked_selective_each_class_precision_at_target": all(
            _finite_float(row.get("precision")) >= required_precision
            for row in (buy, sell)
        ),
        "locked_selective_each_class_wilson_lower_at_target": all(
            _finite_float(row.get("wilson_lower_95")) >= required_precision
            for row in (buy, sell)
        ),
        "locked_selective_spans_ten_source_clusters": int(
            selected_cluster.get("sources", 0) or 0
        )
        >= minimum_selected_sources,
        "locked_selective_source_cluster_lower_95_at_target": _finite_float(
            selected_cluster.get("lower_95")
        )
        >= required_precision,
    }
    checks = {**path_checks, **auxiliary_checks}
    failed = [name for name, passed in path_checks.items() if not passed]
    point_selective_pass = bool(
        auxiliary_checks["locked_selective_accuracy_at_target"]
        and auxiliary_checks["locked_selective_macro_precision_at_target"]
        and auxiliary_checks["locked_selective_each_class_has_minimum_support"]
        and auxiliary_checks["locked_selective_each_class_precision_at_target"]
    )
    robust_selective_pass = bool(
        point_selective_pass
        and auxiliary_checks["locked_selective_overall_wilson_lower_at_target"]
        and auxiliary_checks["locked_selective_each_class_wilson_lower_at_target"]
        and auxiliary_checks["locked_selective_spans_ten_source_clusters"]
        and auxiliary_checks["locked_selective_source_cluster_lower_95_at_target"]
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
        "minimum_endpoint_side_support": minimum_endpoint_side_support,
        "test_class_recalls": {
            side: round(value, 6) for side, value in recalls.items()
        },
        "balanced_accuracy_margin_over_persistence": round(
            balanced - persistence_balanced, 6
        ),
        "endpoint_accuracy_margin_over_path_persistence": round(
            endpoint_accuracy - endpoint_persistence,
            6,
        ),
    }


def _tensor_list(value: torch.Tensor) -> Any:
    return cast(Callable[[], Any], getattr(value, "tolist"))()


def _resolved_key(path: Path | str) -> str:
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _split_manifest_source_map(path: Path) -> dict[str, dict[str, str]]:
    """Map every manifest path to its split and independent perceptual group.

    A source image is not necessarily an independent sample: the clean-split
    manifest deliberately keeps near-duplicate screenshots in one
    ``group_index``.  Confidence intervals and release gates must therefore
    count those groups, not individual files from the same chart capture.
    """

    if not path.exists():
        return {}
    output: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            split = str(row.get("split") or "train").strip().lower()
            group_index = str(row.get("group_index") or "").strip()
            for key in ("source_path", "destination_path"):
                value = row.get(key)
                if value:
                    resolved = _resolved_key(value)
                    independent_group = (
                        f"{split}:perceptual-group:{group_index}"
                        if group_index
                        else f"{split}:source:{resolved}"
                    )
                    output[resolved] = {
                        "split": split,
                        "independent_group": independent_group,
                    }
    return output


def extract_raw_candles(
    image_path: Path,
) -> tuple[list[dict[str, Any]], tuple[int, int]]:
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


def image_to_sequence_features(
    image_path: Path, *, phase: str = ""
) -> list[dict[str, Any]]:
    candles, image_size = extract_raw_candles(image_path)
    # Training input must come from pixels, not hindsight encoded in a filename.
    return candle_sequence_features(
        candles, image_size=image_size, sequence_phase=phase
    )


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
    path.write_text(
        "\n".join(json.dumps(dict(row), separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )


def _raw_suite_sequences(
    root: Path,
    *,
    split_manifest_path: Path,
    cache_path: Path,
    rebuild_cache: bool,
) -> list[dict[str, Any]]:
    split_map = _split_manifest_source_map(split_manifest_path)
    images = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    cached_rows = [] if rebuild_cache else _load_sequence_cache(cache_path)
    cached_by_source = {
        str(row.get("source") or "").casefold(): row for row in cached_rows
    }
    image_keys = {_resolved_key(path) for path in images}
    cache_dirty = bool(rebuild_cache or set(cached_by_source) != image_keys)
    output: list[dict[str, Any]] = []
    for index, image_path in enumerate(images, start=1):
        key = _resolved_key(image_path)
        stat = image_path.stat()
        cached = _mapping(cached_by_source.get(key))
        if (
            cached
            and int(cached.get("source_size", -1)) == int(stat.st_size)
            and int(cached.get("source_mtime_ns", -1)) == int(stat.st_mtime_ns)
            and str(cached.get("extractor_schema_version") or "")
            == EXTRACTOR_SCHEMA_VERSION
            and cached.get("features")
        ):
            row = cached
        else:
            cache_dirty = True
            raw_candles, image_size = extract_raw_candles(image_path)
            features = candle_sequence_features(raw_candles, image_size=image_size)
            row = {
                "source": key,
                "source_path": str(image_path),
                "source_size": int(stat.st_size),
                "source_mtime_ns": int(stat.st_mtime_ns),
                "extractor_schema_version": EXTRACTOR_SCHEMA_VERSION,
                "image_size": [int(image_size[0]), int(image_size[1])],
                "features": features,
            }
        manifest_metadata = _mapping(split_map.get(key))
        resolved_split = str(manifest_metadata.get("split") or "unassigned").lower()
        independent_group = str(
            manifest_metadata.get("independent_group")
            or f"{resolved_split}:source:{key}"
        )
        if str(row.get("split") or "").lower() != resolved_split:
            cache_dirty = True
        if str(row.get("independent_group") or "") != independent_group:
            cache_dirty = True
        row["split"] = resolved_split
        row["independent_group"] = independent_group
        output.append(row)
        if index % 50 == 0:
            print(
                json.dumps(
                    {
                        "stage": "raw_candle_extraction",
                        "processed": index,
                        "total": len(images),
                    }
                ),
                flush=True,
            )
    if cache_dirty:
        _write_sequence_cache(cache_path, output)
    return output


def _play_target(
    history: Sequence[Mapping[str, Any]], future: Sequence[Mapping[str, Any]]
) -> int:
    context_rows = history[-min(8, len(history)) :]
    context_move = sum(
        float(row.get("relative_price_delta_scaled", 0.0)) for row in context_rows
    )
    if abs(context_move) < 0.05 and history:
        context_move = float(history[-1].get("direction_value", 0.0))
    future_moves = [
        float(row.get("relative_price_delta_scaled", 0.0)) for row in future
    ]
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


def _direct_cumulative_path_targets(
    future: Sequence[Mapping[str, Any]],
) -> tuple[list[list[float]], list[int]]:
    """Build direct horizon targets from the last observed candle anchor.

    The raw feature stream stores one-step close displacement.  Forecast rows,
    however, must describe where each future close sits relative to the same
    observed anchor.  Keeping that contract direct avoids an autoregressive
    integration drift where a small one-sided error is summed twelve times.

    The existing two-class direction target remains the candle-body colour.
    Movement direction is derived separately from each event-to-event close
    displacement and includes a small HOLD zone for visually immaterial moves.
    """

    cumulative_displacement = 0.0
    target_rows: list[list[float]] = []
    movement_directions: list[int] = []
    hold_index = PATH_DIRECTION_LABELS.index("HOLD")
    for row in future:
        event_displacement = float(row.get(PATH_TARGET_FEATURE, 0.0) or 0.0)
        cumulative_displacement += event_displacement
        clipped_displacement = max(-1.0, min(1.0, cumulative_displacement))
        values = feature_vector(row)
        values[PATH_TARGET_FEATURE_INDEX] = clipped_displacement
        target_rows.append(values)
        if event_displacement > PATH_DIRECTION_HOLD_THRESHOLD_SCALED:
            movement_directions.append(SIDE_TO_INDEX["BUY"])
        elif event_displacement < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED:
            movement_directions.append(SIDE_TO_INDEX["SELL"])
        else:
            movement_directions.append(hold_index)
    return target_rows, movement_directions


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
        features = [
            dict(cast(Mapping[str, Any], row))
            for row in cast(Sequence[Any], sequence_row.get("features", []))
            if isinstance(row, Mapping)
        ]
        if len(features) < minimum_history + horizon_steps:
            continue
        if str(sequence_row.get("split") or "") not in {
            "train",
            "val",
            "valid",
            "validation",
            "test",
        }:
            continue
        sequence_quality = candle_sequence_geometry_quality(
            features,
            image_size=cast(Sequence[int], sequence_row.get("image_size") or (0, 0)),
            minimum_events=minimum_history + horizon_steps,
        )
        if not bool(sequence_quality.get("ready")):
            continue
        parse_values = [
            float(row.get("parse_confidence", 0.0) or 0.0) for row in features
        ]
        median_confidence = (
            float(np.median(np.asarray(parse_values, dtype=np.float32)))
            if parse_values
            else 0.0
        )
        clipped_rate = sum(
            abs(float(row.get("relative_price_delta_scaled", 0.0) or 0.0)) >= 0.999
            for row in features[1:]
        ) / max(1, len(features) - 1)
        if median_confidence < float(minimum_source_confidence) or clipped_rate > float(
            maximum_clipped_delta_rate
        ):
            continue
        cut_points = _evenly_spaced(
            list(range(int(minimum_history), len(features) - int(horizon_steps) + 1)),
            int(windows_per_source),
        )
        for cut in cut_points:
            history = features[max(0, cut - sequence_length) : cut]
            future = features[cut : cut + horizon_steps]
            matrix = [feature_vector(row) for row in history[-sequence_length:]]
            matrix.extend(
                [
                    [0.0] * len(FEATURE_SCHEMA)
                    for _ in range(max(0, sequence_length - len(matrix)))
                ]
            )
            future_matrix, movement_directions = _direct_cumulative_path_targets(future)
            history_bbox: Sequence[Any] = (
                cast(Sequence[Any], history[-1].get("bbox", ())) if history else ()
            )
            chart_cut_x = (
                float(history_bbox[2]) + 1.0 if len(history_bbox) >= 4 else 0.0
            )
            source_path = str(
                sequence_row.get("source_path") or sequence_row.get("source") or ""
            )
            windows.append(
                {
                    "sequence": matrix,
                    "length": min(len(history), sequence_length),
                    "targets": future_matrix,
                    "directions": [
                        SIDE_TO_INDEX["BUY"]
                        if float(row.get("direction_value", 0.0)) >= 0.0
                        else SIDE_TO_INDEX["SELL"]
                        for row in future
                    ],
                    "movement_directions": movement_directions,
                    "target_quality": [
                        max(0.15, float(row.get("parse_confidence", 0.0)))
                        for row in future
                    ],
                    "play": _play_target(history, future),
                    "split": str(sequence_row.get("split") or "train").lower(),
                    "source": source_path,
                    "independent_group": str(
                        sequence_row.get("independent_group")
                        or f"{sequence_row.get('split')}:source:{_resolved_key(source_path)}"
                    ),
                    "cut_point": cut,
                    "chart_cut_x": chart_cut_x,
                    "window_id": hashlib.sha256(
                        f"{source_path}|{cut}".encode("utf-8")
                    ).hexdigest()[:20],
                    "source_median_parse_confidence": median_confidence,
                    "source_clipped_delta_rate": clipped_rate,
                    "source_sequence_quality": sequence_quality,
                }
            )
    return windows


_CHART_CONTEXT_CACHE: dict[tuple[str, int, int, int], torch.Tensor] = {}


def _chart_context_cache_key(
    row: Mapping[str, Any],
) -> tuple[str, int, int, int] | None:
    source = Path(str(row.get("source") or ""))
    cut_x = int(round(float(row.get("chart_cut_x", 0.0) or 0.0)))
    try:
        mtime_ns = int(source.stat().st_mtime_ns)
    except OSError:
        return None
    return (_resolved_key(source), mtime_ns, cut_x, CHART_CONTEXT_SIZE[1])


def _prewarm_chart_context_cache(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int | float]:
    """Decode each source screenshot once, then materialize all causal cuts.

    Each screenshot normally contributes up to eight windows. Opening and
    resizing the same OneDrive-backed PNG once per window dominated the first
    epoch, so grouped prewarming keeps the exact tensors while avoiding that
    repeated I/O and decode work.
    """

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        source = str(row.get("source") or "")
        key = _chart_context_cache_key(row)
        if source and key is not None and key not in _CHART_CONTEXT_CACHE:
            grouped.setdefault(_resolved_key(source), []).append(row)
    cached_before = len(_CHART_CONTEXT_CACHE)
    failed_sources = 0
    for source_index, source_rows in enumerate(grouped.values(), start=1):
        source = Path(str(source_rows[0].get("source") or ""))
        try:
            with Image.open(source) as opened:
                image = opened.convert("RGB")
            image.thumbnail((1920, 1200), Image.Resampling.BILINEAR)
            width, height = image.size
            x0, x1 = int(width * 0.06), int(width * 0.92)
            y0, y1 = int(height * 0.05), int(height * 0.96)
            chart = image.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)))
            uncached_rows = [
                row
                for row in source_rows
                if (
                    (cache_key := _chart_context_cache_key(row)) is not None
                    and cache_key not in _CHART_CONTEXT_CACHE
                )
            ]
            if not uncached_rows:
                continue
            chart_array = np.array(chart, dtype=np.uint8, copy=True)
            torch_from_numpy = cast(
                Callable[[object], torch.Tensor],
                getattr(torch, "from_numpy"),
            )
            base = (
                torch_from_numpy(chart_array)
                .permute(2, 0, 1)
                .to(dtype=torch.float32)
                .div_(255.0)
            )
            batch = base.unsqueeze(0).expand(len(uncached_rows), -1, -1, -1).clone()
            local_cuts: list[int] = []
            for batch_index, row in enumerate(uncached_rows):
                cut_x = int(round(float(row.get("chart_cut_x", 0.0) or 0.0)))
                local_cut = max(0, min(chart.width, cut_x - x0))
                local_cuts.append(local_cut)
                batch[batch_index, :, :, local_cut:] = 0.0
            resized = torch.nn.functional.interpolate(
                batch,
                size=CHART_CONTEXT_SIZE,
                mode="bilinear",
                align_corners=False,
            )
            for batch_index, row in enumerate(uncached_rows):
                cache_key = _chart_context_cache_key(row)
                if cache_key is None or cache_key in _CHART_CONTEXT_CACHE:
                    continue
                context = resized[batch_index].clone()
                resized_cut = int(
                    round(
                        local_cuts[batch_index]
                        * CHART_CONTEXT_SIZE[1]
                        / max(1, chart.width)
                    )
                )
                context[:, :, max(0, min(CHART_CONTEXT_SIZE[1], resized_cut)) :] = 0.0
                if len(_CHART_CONTEXT_CACHE) < 4096:
                    _CHART_CONTEXT_CACHE[cache_key] = context.detach().clone()
        except (OSError, ValueError, TypeError):
            failed_sources += 1
            for row in source_rows:
                cache_key = _chart_context_cache_key(row)
                if cache_key is not None and len(_CHART_CONTEXT_CACHE) < 4096:
                    _CHART_CONTEXT_CACHE[cache_key] = torch.zeros(
                        (3, *CHART_CONTEXT_SIZE),
                        dtype=torch.float32,
                    )
        if source_index % 50 == 0:
            print(
                json.dumps(
                    {
                        "stage": "chart_context_prewarm",
                        "processed_sources": source_index,
                        "total_sources": len(grouped),
                    }
                ),
                flush=True,
            )
    return {
        "sources": len(grouped),
        "contexts": len(_CHART_CONTEXT_CACHE) - cached_before,
        "failed_sources": failed_sources,
    }


def _chart_context_for_row(row: Mapping[str, Any]) -> torch.Tensor:
    source = Path(str(row.get("source") or ""))
    cut_x = int(round(float(row.get("chart_cut_x", 0.0) or 0.0)))
    cache_key = _chart_context_cache_key(row)
    if cache_key is None:
        return torch.zeros((3, *CHART_CONTEXT_SIZE), dtype=torch.float32)
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


def _augment_training_batch(
    sequence: torch.Tensor,
    context: torch.Tensor,
    lengths: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Vectorized raw-suite augmentation with the causal mask preserved."""

    batch_size = int(sequence.shape[0])
    sequence_values = sequence.clone()
    sequence_noise = torch.randn_like(sequence_values) * 0.008
    sequence_noise[:, :, FEATURE_SCHEMA.index("direction_value")] = 0.0
    positions = torch.arange(sequence_values.shape[1], device=sequence_values.device)
    valid_history = positions.unsqueeze(0) < lengths.to(
        sequence_values.device
    ).unsqueeze(1)
    noisy_rows = torch.rand((batch_size, 1), device=sequence_values.device) < 0.45
    sequence_noise *= (valid_history & noisy_rows).unsqueeze(-1)
    sequence_values += sequence_noise

    context_values = context.clone()
    # Columns to the right of the historical cut are exactly zero. Preserve
    # that mask after all stochastic transforms so augmentation can never
    # synthesize a fake future region.
    causal_columns = torch.any(context_values != 0.0, dim=(1, 2), keepdim=True)
    gamma = torch.empty((batch_size, 1, 1, 1), dtype=context_values.dtype).uniform_(
        0.82, 1.20
    )
    gain = torch.empty((batch_size, 1, 1, 1), dtype=context_values.dtype).uniform_(
        0.82, 1.18
    )
    photometric = torch.clamp(context_values.pow(gamma) * gain, 0.0, 1.0)
    photometric_rows = torch.rand((batch_size, 1, 1, 1)) < 0.80
    context_values = torch.where(photometric_rows, photometric, context_values)

    sigma = torch.empty((batch_size, 1, 1, 1), dtype=context_values.dtype).uniform_(
        0.002, 0.018
    )
    noisy_context = torch.clamp(
        context_values + torch.randn_like(context_values) * sigma, 0.0, 1.0
    )
    context_noise_rows = torch.rand((batch_size, 1, 1, 1)) < 0.35
    context_values = torch.where(context_noise_rows, noisy_context, context_values)
    selected_batch_indices = torch.nonzero(
        torch.rand(batch_size) < 0.30,
        as_tuple=False,
    ).flatten()
    for raw_batch_index in selected_batch_indices:
        batch_index = int(raw_batch_index.item())
        for _ in range(random.randint(1, 3)):
            y = random.randrange(context_values.shape[2])
            color = torch.rand((3, 1), dtype=context_values.dtype) * 0.85
            context_values[batch_index, :, y : y + 1, :] = color.unsqueeze(-1)
    context_values *= causal_columns
    return sequence_values, context_values


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
    def __init__(
        self, rows: Sequence[Mapping[str, Any]], *, augment: bool = False
    ) -> None:
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
        return (
            sequence,
            torch.tensor(row["targets"], dtype=torch.float32),
            torch.tensor(row["directions"], dtype=torch.long),
            torch.tensor(int(row["play"]), dtype=torch.long),
            torch.tensor(
                int(row.get("length", len(row["sequence"]))), dtype=torch.long
            ),
            context,
            torch.tensor(
                row.get("target_quality", [1.0] * len(row["directions"])),
                dtype=torch.float32,
            ),
            torch.tensor(index, dtype=torch.long),
        )


def _split_rows(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    train = [row for row in rows if str(row.get("split")) == "train"]
    validation = [
        row for row in rows if str(row.get("split")) in {"val", "valid", "validation"}
    ]
    test = [row for row in rows if str(row.get("split")) == "test"]
    return train, validation, test


def _balanced_accuracy(y_true: Sequence[int], y_pred: Sequence[int]) -> float:
    recalls: list[float] = []
    for label in (0, 1):
        indices = [index for index, value in enumerate(y_true) if value == label]
        if indices:
            recalls.append(
                sum(int(y_pred[index] == label) for index in indices) / len(indices)
            )
    return sum(recalls) / len(recalls) if recalls else 0.0


def _multiclass_balanced_accuracy(
    y_true: Sequence[int], y_pred: Sequence[int], labels: int
) -> float:
    recalls: list[float] = []
    for label in range(labels):
        indices = [index for index, value in enumerate(y_true) if value == label]
        if indices:
            recalls.append(
                sum(int(y_pred[index] == label) for index in indices) / len(indices)
            )
    return sum(recalls) / len(recalls) if recalls else 0.0


def _continuous_targets(targets: torch.Tensor) -> torch.Tensor:
    return torch.stack(
        [targets[:, :, index] for index in PREDICTION_FEATURE_INDICES], dim=-1
    )


def _class_weights(rows: Sequence[Mapping[str, Any]]) -> torch.Tensor:
    counts = [1, 1]
    for row in rows:
        for label in cast(Sequence[int], row.get("directions", [])):
            counts[int(label)] += 1
    total = float(sum(counts))
    return torch.tensor(
        [total / (2.0 * count) for count in counts], dtype=torch.float32
    )


def _play_class_weights(rows: Sequence[Mapping[str, Any]]) -> torch.Tensor:
    counts = [1 for _ in PLAY_LABELS]
    for row in rows:
        counts[int(row.get("play", PLAY_TO_INDEX["PULLBACK"]))] += 1
    total = float(sum(counts))
    raw = [math.sqrt(total / (len(PLAY_LABELS) * count)) for count in counts]
    mean_weight = sum(raw) / len(raw)
    return torch.tensor([weight / mean_weight for weight in raw], dtype=torch.float32)


def _trajectory_mode_targets(path_targets: torch.Tensor) -> torch.Tensor:
    """Classify each window by its final anchor-relative close displacement."""

    if path_targets.ndim != 2 or int(path_targets.shape[1]) < 1:
        raise ValueError("path_targets must have shape [B, H] with H >= 1")
    hold_index = PATH_DIRECTION_LABELS.index("HOLD")
    targets = torch.full(
        (int(path_targets.shape[0]),),
        hold_index,
        dtype=torch.long,
        device=path_targets.device,
    )
    endpoint = path_targets[:, -1]
    targets[endpoint > PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = SIDE_TO_INDEX["BUY"]
    targets[endpoint < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = SIDE_TO_INDEX["SELL"]
    return targets


def _trajectory_mode_class_weights(
    rows: Sequence[Mapping[str, Any]],
) -> torch.Tensor:
    counts = [1 for _label in PATH_DIRECTION_LABELS]
    for row in rows:
        target_rows = cast(Sequence[Any], row.get("targets", []))
        if not target_rows:
            continue
        endpoint = cast(Sequence[Any], target_rows[-1])
        if len(endpoint) <= PATH_TARGET_FEATURE_INDEX:
            continue
        displacement = _finite_float(endpoint[PATH_TARGET_FEATURE_INDEX])
        label = (
            SIDE_TO_INDEX["BUY"]
            if displacement > PATH_DIRECTION_HOLD_THRESHOLD_SCALED
            else SIDE_TO_INDEX["SELL"]
            if displacement < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED
            else PATH_DIRECTION_LABELS.index("HOLD")
        )
        counts[label] += 1
    total = float(sum(counts))
    raw = [math.sqrt(total / (len(PATH_DIRECTION_LABELS) * count)) for count in counts]
    mean_weight = sum(raw) / len(raw)
    return torch.tensor(
        [weight / mean_weight for weight in raw],
        dtype=torch.float32,
    )


def _dead_zone_direction_hinge(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    *,
    margin: float = PATH_DIRECTION_HOLD_THRESHOLD_SCALED,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Penalize a wrong/weak sign without rewarding exaggerated magnitude.

    BUY and SELL targets only need to clear the same dead-zone used by the
    three-way path labels. Once that margin is reached the loss is exactly
    zero, unlike a binary-logit loss whose gradient keeps driving a correctly
    signed displacement toward the tanh boundary.
    """

    resolved_margin = max(0.0, float(margin))
    buy_mask = targets > resolved_margin
    sell_mask = targets < -resolved_margin
    active_mask = buy_mask | sell_mask
    losses = torch.where(
        buy_mask,
        F.relu(resolved_margin - predictions),
        torch.where(
            sell_mask,
            F.relu(resolved_margin + predictions),
            torch.zeros_like(predictions),
        ),
    )
    return losses, active_mask


def _loss(
    outputs: Mapping[str, torch.Tensor],
    targets: torch.Tensor,
    directions: torch.Tensor,
    plays: torch.Tensor,
    *,
    class_weights: torch.Tensor,
    play_class_weights: torch.Tensor,
    target_quality: torch.Tensor,
    trajectory_mode_class_weights: torch.Tensor | None = None,
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
    target_displacements = continuous[:, :, PATH_PREDICTION_INDEX]
    trajectory_targets = _trajectory_mode_targets(target_displacements)
    regression_mean = feature_mean
    regression_scale = feature_scale
    trajectory_mode_natural_loss = torch.sum(feature_mean) * 0.0
    trajectory_mode_balanced_loss = torch.sum(feature_mean) * 0.0
    trajectory_mode_brier = torch.sum(feature_mean) * 0.0
    trajectory_mode_sign_loss = torch.sum(feature_mean) * 0.0
    trajectory_decoder_enabled = all(
        key in outputs
        for key in (
            "trajectory_mode_logits",
            "trajectory_mode_mean",
            "trajectory_mode_scale",
        )
    )
    if trajectory_decoder_enabled:
        mode_logits = outputs["trajectory_mode_logits"]
        mode_means = outputs["trajectory_mode_mean"]
        mode_scales = outputs["trajectory_mode_scale"]
        expected_mode_shape = (
            int(targets.shape[0]),
            int(targets.shape[1]),
            len(PATH_DIRECTION_LABELS),
        )
        if (
            tuple(mode_means.shape) != expected_mode_shape
            or tuple(mode_scales.shape) != expected_mode_shape
        ):
            raise ValueError(
                f"trajectory mode means/scales must have shape {expected_mode_shape}"
            )
        gather_index = trajectory_targets[:, None, None].expand(
            -1,
            int(targets.shape[1]),
            1,
        )
        selected_means = torch.gather(mode_means, 2, gather_index).squeeze(2)
        selected_scales = torch.gather(mode_scales, 2, gather_index).squeeze(2)
        regression_mean = feature_mean.clone()
        regression_scale = feature_scale.clone()
        regression_mean[:, :, PATH_PREDICTION_INDEX] = selected_means
        regression_scale[:, :, PATH_PREDICTION_INDEX] = selected_scales

        trajectory_mode_natural_loss = F.cross_entropy(
            mode_logits,
            trajectory_targets,
        )
        resolved_mode_weights = (
            trajectory_mode_class_weights
            if trajectory_mode_class_weights is not None
            else torch.ones(
                len(PATH_DIRECTION_LABELS),
                dtype=mode_logits.dtype,
                device=mode_logits.device,
            )
        )
        trajectory_mode_balanced_loss = F.cross_entropy(
            mode_logits,
            trajectory_targets,
            weight=resolved_mode_weights.to(mode_logits.device),
        )
        mode_probabilities = torch.softmax(mode_logits, dim=-1)
        mode_one_hot = F.one_hot(
            trajectory_targets,
            num_classes=len(PATH_DIRECTION_LABELS),
        ).to(dtype=mode_probabilities.dtype)
        trajectory_mode_brier = torch.mean(
            torch.sum((mode_probabilities - mode_one_hot) ** 2, dim=-1)
        )

        # Every alternative is a labelled conditional scenario, not just an
        # arbitrary mixture component. Keep endpoints on the correct side of
        # the HOLD zone even when that branch has low probability. These are
        # true hinges: once a branch clears its labelled margin, there is no
        # gradient encouraging a visually dramatic but unsupported endpoint.
        endpoint_modes = mode_means[:, -1, :]
        margin = PATH_DIRECTION_HOLD_THRESHOLD_SCALED
        buy_sign = F.relu(margin - endpoint_modes[:, SIDE_TO_INDEX["BUY"]])
        sell_sign = F.relu(margin + endpoint_modes[:, SIDE_TO_INDEX["SELL"]])
        hold_endpoint = endpoint_modes[:, PATH_DIRECTION_LABELS.index("HOLD")]
        hold_sign = F.relu(torch.abs(hold_endpoint) - margin)
        trajectory_mode_sign_loss = torch.mean(buy_sign + sell_sign + hold_sign)

    residual = (continuous - regression_mean) / regression_scale
    # Heavy-tailed Student-t objective is robust to screenshot compression and
    # imperfect wick segmentation; unlike Gaussian NLL it does not let a few
    # parser outliers dominate all twelve horizons.
    student_nll = torch.log(regression_scale) + 2.0 * torch.log1p(
        residual.square() / 3.0
    )
    student_nll = torch.mean(student_nll * quality.unsqueeze(-1))
    point_huber = F.smooth_l1_loss(regression_mean, continuous, reduction="none")
    point_huber = torch.mean(point_huber * quality.unsqueeze(-1))

    predicted_displacements = regression_mean[:, :, PATH_PREDICTION_INDEX]
    path_huber_values = F.smooth_l1_loss(
        predicted_displacements,
        target_displacements,
        reduction="none",
    )
    path_huber = torch.mean(path_huber_values * quality)

    position_mask = (
        torch.abs(target_displacements) > PATH_DIRECTION_HOLD_THRESHOLD_SCALED
    )
    if trajectory_decoder_enabled:
        position_losses, position_mask = _dead_zone_direction_hinge(
            predicted_displacements,
            target_displacements,
        )
    else:
        position_targets = (target_displacements > 0.0).to(
            dtype=predicted_displacements.dtype
        )
        position_losses = F.binary_cross_entropy_with_logits(
            PATH_DIRECTION_LOGIT_SCALE * predicted_displacements,
            position_targets,
            reduction="none",
        )
    position_direction_loss = (
        torch.mean((position_losses * quality)[position_mask])
        if bool(torch.any(position_mask))
        else torch.sum(predicted_displacements) * 0.0
    )
    position_hold_losses = F.smooth_l1_loss(
        predicted_displacements,
        torch.zeros_like(predicted_displacements),
        reduction="none",
    )
    position_hold_loss = (
        torch.mean((position_hold_losses * quality)[~position_mask])
        if bool(torch.any(~position_mask))
        else torch.sum(predicted_displacements) * 0.0
    )

    # The direction heads still classify candle-body colour.  Close movement
    # is supervised from the direct displacement channel instead of forcing a
    # body/movement agreement that is false for candles with long wicks.
    zero_anchor = torch.zeros_like(predicted_displacements[:, :1])
    predicted_event_moves = torch.diff(
        torch.cat((zero_anchor, predicted_displacements), dim=1),
        dim=1,
    )
    target_event_moves = torch.diff(
        torch.cat((zero_anchor, target_displacements), dim=1),
        dim=1,
    )
    movement_mask = torch.abs(target_event_moves) > PATH_DIRECTION_HOLD_THRESHOLD_SCALED
    if trajectory_decoder_enabled:
        movement_losses, movement_mask = _dead_zone_direction_hinge(
            predicted_event_moves,
            target_event_moves,
        )
    else:
        movement_targets = (target_event_moves > 0.0).to(
            dtype=predicted_event_moves.dtype
        )
        movement_losses = F.binary_cross_entropy_with_logits(
            PATH_DIRECTION_LOGIT_SCALE * predicted_event_moves,
            movement_targets,
            reduction="none",
        )
    movement_direction_loss = (
        torch.mean((movement_losses * quality)[movement_mask])
        if bool(torch.any(movement_mask))
        else torch.sum(predicted_event_moves) * 0.0
    )
    hold_mask = ~movement_mask
    hold_losses = F.smooth_l1_loss(
        predicted_event_moves,
        torch.zeros_like(predicted_event_moves),
        reduction="none",
    )
    movement_hold_loss = (
        torch.mean((hold_losses * quality)[hold_mask])
        if bool(torch.any(hold_mask))
        else torch.sum(predicted_event_moves) * 0.0
    )

    endpoint_predictions = predicted_displacements[:, -1]
    endpoint_targets = target_displacements[:, -1]
    endpoint_mask = torch.abs(endpoint_targets) > PATH_DIRECTION_HOLD_THRESHOLD_SCALED
    if trajectory_decoder_enabled:
        endpoint_losses, endpoint_mask = _dead_zone_direction_hinge(
            endpoint_predictions,
            endpoint_targets,
        )
    else:
        endpoint_direction_targets = (endpoint_targets > 0.0).to(
            dtype=endpoint_predictions.dtype
        )
        endpoint_losses = F.binary_cross_entropy_with_logits(
            PATH_DIRECTION_LOGIT_SCALE * endpoint_predictions,
            endpoint_direction_targets,
            reduction="none",
        )
    endpoint_loss = (
        torch.mean((endpoint_losses * quality[:, -1])[endpoint_mask])
        if bool(torch.any(endpoint_mask))
        else torch.sum(endpoint_predictions) * 0.0
    )
    endpoint_hold_loss = (
        F.smooth_l1_loss(
            endpoint_predictions[~endpoint_mask],
            torch.zeros_like(endpoint_predictions[~endpoint_mask]),
        )
        if bool(torch.any(~endpoint_mask))
        else torch.sum(endpoint_predictions) * 0.0
    )
    play_loss = F.cross_entropy(
        outputs["play_logits"], plays, weight=play_class_weights.to(plays.device)
    )
    return (
        # Body colour and play are useful auxiliary regularizers, but the
        # deployable product is the path.  Keep their gradients subordinate to
        # direct position, event movement, and endpoint supervision.
        0.18 * torch.mean(natural_ce * quality)
        + 0.10 * torch.mean(balanced_ce * quality)
        + 0.025 * brier
        + 0.20 * student_nll
        + 0.18 * point_huber
        + 0.85 * path_huber
        + 0.65 * position_direction_loss
        + 0.12 * position_hold_loss
        + 0.55 * movement_direction_loss
        + 0.15 * movement_hold_loss
        + 0.45 * endpoint_loss
        + 0.10 * endpoint_hold_loss
        + 0.55 * trajectory_mode_natural_loss
        + 0.25 * trajectory_mode_balanced_loss
        + 0.05 * trajectory_mode_brier
        + 0.25 * trajectory_mode_sign_loss
        + 0.01 * play_loss
        + 0.003 * torch.mean(regression_scale)
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
            "path_interval_90_marginal_coverage": 0.0,
            "path_movement_direction_accuracy": 0.0,
            "path_movement_balanced_accuracy": 0.0,
            "horizon_position_direction_accuracy": {},
            "horizon_position_balanced_accuracy": 0.0,
            "endpoint_path_balanced_accuracy": 0.0,
            "endpoint_path_persistence_accuracy": 0.0,
            "endpoint_path_persistence_balanced_accuracy": 0.0,
            "endpoint_predicted_support": {"BUY": 0, "SELL": 0, "HOLD": 0},
            "endpoint_source_cluster_accuracy_95": {},
            "trajectory_mode_accuracy": None,
            "trajectory_mode_balanced_accuracy": None,
            "trajectory_target_mode_path_delta_mae": None,
            "trajectory_map_path_delta_mae": None,
            "play_accuracy": 0.0,
            "play_balanced_accuracy": 0.0,
            "play_majority_baseline_accuracy": 0.0,
            "calibration_error": 1.0,
            "persistence_baseline_accuracy": 0.0,
            "persistence_baseline_balanced_accuracy": 0.0,
            "horizon_direction_accuracy": {},
            "horizon_path_movement_accuracy": {},
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
    per_step_movement_true: list[list[int]] = []
    per_step_movement_pred: list[list[int]] = []
    movement_true_all: list[int] = []
    movement_pred_all: list[int] = []
    per_step_position_true: list[list[int]] = []
    per_step_position_pred: list[list[int]] = []
    position_true_all: list[int] = []
    position_pred_all: list[int] = []
    endpoint_true_all: list[int] = []
    endpoint_pred_all: list[int] = []
    endpoint_source_ids: list[str] = []
    endpoint_persistence_pred: list[int] = []
    trajectory_mode_true_all: list[int] = []
    trajectory_mode_pred_all: list[int] = []
    trajectory_mode_probabilities_all: list[list[float]] = []
    play_true: list[int] = []
    play_pred: list[int] = []
    persistence_correct = 0
    persistence_total = 0
    persistence_predictions: list[int] = []
    delta_error = 0.0
    target_mode_delta_error = 0.0
    target_mode_delta_count = 0
    delta_count = 0
    covered = 0
    path_marginal_covered = 0
    endpoint_correct = 0
    endpoint_total = 0
    confusion = [[0, 0], [0, 0]]
    natural_confusion = [[0, 0], [0, 0]]
    window_embeddings: list[list[float]] = []
    window_sources: list[str] = []
    window_independent_groups: list[str] = []
    window_directions: list[list[int]] = []
    window_deltas: list[list[float]] = []
    window_path_means: list[list[float]] = []
    window_path_scales: list[list[float]] = []
    window_path_targets: list[list[float]] = []
    window_indices: list[int] = []
    model.eval()
    dataset = CandlePathDataset(rows)
    with torch.inference_mode():
        for (
            sequence,
            targets,
            directions,
            plays,
            lengths,
            chart_context,
            _target_quality,
            row_indices,
        ) in DataLoader(
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
                per_step_movement_true = [[] for _ in range(directions.shape[1])]
                per_step_movement_pred = [[] for _ in range(directions.shape[1])]
                per_step_position_true = [[] for _ in range(directions.shape[1])]
                per_step_position_pred = [[] for _ in range(directions.shape[1])]
            for step in range(directions.shape[1]):
                per_step_true[step].extend(
                    int(value)
                    for value in cast(list[int], _tensor_list(directions[:, step]))
                )
                per_step_pred[step].extend(
                    int(value)
                    for value in cast(list[int], _tensor_list(predictions[:, step]))
                )
            direction_values = cast(list[list[int]], _tensor_list(directions))
            prediction_values = cast(list[list[int]], _tensor_list(predictions))
            natural_prediction_values = cast(
                list[list[int]], _tensor_list(natural_predictions)
            )
            natural_probability_values = cast(
                list[list[list[float]]], _tensor_list(natural_probabilities)
            )
            decision_probability_values = cast(
                list[list[list[float]]], _tensor_list(decision_probabilities)
            )
            logits_values = cast(
                list[list[list[float]]], _tensor_list(outputs["direction_logits"])
            )
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
                source_id = str(
                    rows[row_index].get("independent_group")
                    or rows[row_index].get("source")
                    or ""
                )
                final_observation = max(0, int(length_values[batch_offset]) - 1)
                persistence_side = (
                    0
                    if float(
                        sequence_row[
                            final_observation, FEATURE_SCHEMA.index("direction_value")
                        ].item()
                    )
                    >= 0.0
                    else 1
                )
                for (
                    true,
                    pred,
                    natural_pred,
                    natural_probability,
                    decision_probability,
                    logit,
                ) in zip(
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
                    natural_probabilities_all.append(
                        [float(value) for value in natural_probability]
                    )
                    decision_probabilities_all.append(
                        [float(value) for value in decision_probability]
                    )
                    source_ids_all.append(source_id)
                    persistence_correct += int(true == persistence_side)
                    persistence_total += 1
                    persistence_predictions.append(persistence_side)
            continuous = _continuous_targets(targets)
            means = outputs["feature_mean"]
            scales = outputs["feature_scale"]
            path_targets = continuous[:, :, PATH_PREDICTION_INDEX]
            mode_targets = _trajectory_mode_targets(path_targets)
            mode_predictions: torch.Tensor | None = None
            evaluation_means = means
            evaluation_scales = scales
            if all(
                key in outputs
                for key in (
                    "trajectory_mode_logits",
                    "trajectory_mode_mean",
                    "trajectory_mode_scale",
                )
            ):
                mode_probabilities = torch.softmax(
                    outputs["trajectory_mode_logits"],
                    dim=-1,
                )
                mode_predictions = torch.argmax(mode_probabilities, dim=-1)
                target_gather_index = mode_targets[:, None, None].expand(
                    -1,
                    int(path_targets.shape[1]),
                    1,
                )
                map_gather_index = mode_predictions[:, None, None].expand(
                    -1,
                    int(path_targets.shape[1]),
                    1,
                )
                target_mode_path_means = torch.gather(
                    outputs["trajectory_mode_mean"],
                    2,
                    target_gather_index,
                ).squeeze(2)
                path_means = torch.gather(
                    outputs["trajectory_mode_mean"],
                    2,
                    map_gather_index,
                ).squeeze(2)
                path_scales = torch.gather(
                    outputs["trajectory_mode_scale"],
                    2,
                    map_gather_index,
                ).squeeze(2)
                evaluation_means = means.clone()
                evaluation_scales = scales.clone()
                evaluation_means[:, :, PATH_PREDICTION_INDEX] = path_means
                evaluation_scales[:, :, PATH_PREDICTION_INDEX] = path_scales
                target_mode_delta_error += MAX_PRICE_DELTA * float(
                    torch.sum(torch.abs(target_mode_path_means - path_targets)).item()
                )
                target_mode_delta_count += int(path_targets.numel())
                trajectory_mode_true_all.extend(
                    int(value) for value in cast(list[int], _tensor_list(mode_targets))
                )
                trajectory_mode_pred_all.extend(
                    int(value)
                    for value in cast(list[int], _tensor_list(mode_predictions))
                )
                trajectory_mode_probabilities_all.extend(
                    [
                        [float(value) for value in row]
                        for row in cast(
                            list[list[float]],
                            _tensor_list(mode_probabilities),
                        )
                    ]
                )
            else:
                path_means = means[:, :, PATH_PREDICTION_INDEX]
                path_scales = scales[:, :, PATH_PREDICTION_INDEX]
            delta_error += MAX_PRICE_DELTA * float(
                torch.sum(torch.abs(path_means - path_targets)).item()
            )
            delta_count += int(path_targets.numel())
            covered += int(
                torch.sum(
                    torch.abs(continuous - evaluation_means)
                    <= 1.645 * evaluation_scales
                ).item()
            )
            path_marginal_covered += int(
                torch.sum(
                    torch.abs(path_targets - path_means) <= 1.645 * path_scales
                ).item()
            )
            hold_index = PATH_DIRECTION_LABELS.index("HOLD")
            position_true = torch.full_like(path_targets, hold_index, dtype=torch.long)
            position_true[path_targets > PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["BUY"]
            )
            position_true[path_targets < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["SELL"]
            )
            position_pred = torch.full_like(path_means, hold_index, dtype=torch.long)
            position_pred[path_means > PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["BUY"]
            )
            position_pred[path_means < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["SELL"]
            )
            zero_anchor = torch.zeros_like(path_targets[:, :1])
            event_targets = torch.diff(
                torch.cat((zero_anchor, path_targets), dim=1), dim=1
            )
            event_means = torch.diff(torch.cat((zero_anchor, path_means), dim=1), dim=1)
            movement_true = torch.full_like(event_targets, hold_index, dtype=torch.long)
            movement_true[event_targets > PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["BUY"]
            )
            movement_true[event_targets < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["SELL"]
            )
            movement_pred = torch.full_like(event_means, hold_index, dtype=torch.long)
            movement_pred[event_means > PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["BUY"]
            )
            movement_pred[event_means < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED] = (
                SIDE_TO_INDEX["SELL"]
            )
            movement_true_values = cast(list[list[int]], _tensor_list(movement_true))
            movement_pred_values = cast(list[list[int]], _tensor_list(movement_pred))
            position_true_values = cast(list[list[int]], _tensor_list(position_true))
            position_pred_values = cast(list[list[int]], _tensor_list(position_pred))
            for step in range(directions.shape[1]):
                per_step_movement_true[step].extend(
                    row[step] for row in movement_true_values
                )
                per_step_movement_pred[step].extend(
                    row[step] for row in movement_pred_values
                )
                per_step_position_true[step].extend(
                    row[step] for row in position_true_values
                )
                per_step_position_pred[step].extend(
                    row[step] for row in position_pred_values
                )
            movement_true_all.extend(
                value for row in movement_true_values for value in row
            )
            movement_pred_all.extend(
                value for row in movement_pred_values for value in row
            )
            position_true_all.extend(
                value for row in position_true_values for value in row
            )
            position_pred_all.extend(
                value for row in position_pred_values for value in row
            )
            predicted_endpoint = (
                mode_predictions
                if mode_predictions is not None
                else position_pred[:, -1]
            )
            actual_endpoint = mode_targets
            endpoint_correct += int(
                torch.sum(predicted_endpoint == actual_endpoint).item()
            )
            endpoint_total += int(predicted_endpoint.numel())
            endpoint_true_all.extend(
                int(value) for value in cast(list[int], _tensor_list(actual_endpoint))
            )
            endpoint_pred_all.extend(
                int(value)
                for value in cast(list[int], _tensor_list(predicted_endpoint))
            )
            endpoint_source_ids.extend(
                str(
                    rows[int(index)].get("independent_group")
                    or rows[int(index)].get("source")
                    or ""
                )
                for index in batch_row_indices
            )
            delta_feature_index = FEATURE_SCHEMA.index("relative_price_delta_scaled")
            for batch_offset, row_index in enumerate(batch_row_indices):
                length = max(1, int(length_values[batch_offset]))
                history_start = max(0, length - min(8, length))
                trailing_move = float(
                    torch.sum(
                        sequence[
                            batch_offset, history_start:length, delta_feature_index
                        ]
                    ).item()
                )
                endpoint_persistence_pred.append(
                    SIDE_TO_INDEX["BUY"]
                    if trailing_move > PATH_DIRECTION_HOLD_THRESHOLD_SCALED
                    else SIDE_TO_INDEX["SELL"]
                    if trailing_move < -PATH_DIRECTION_HOLD_THRESHOLD_SCALED
                    else hold_index
                )
            play_true.extend(
                int(value) for value in cast(list[int], _tensor_list(plays))
            )
            play_pred.extend(
                int(value) for value in cast(list[int], _tensor_list(play_predictions))
            )
            window_embeddings.extend(
                [
                    list(map(float, row))
                    for row in cast(
                        list[list[float]], _tensor_list(outputs["context_embedding"])
                    )
                ]
            )
            window_sources.extend(
                str(rows[int(index)].get("source") or "") for index in batch_row_indices
            )
            window_independent_groups.extend(
                str(
                    rows[int(index)].get("independent_group")
                    or rows[int(index)].get("source")
                    or ""
                )
                for index in batch_row_indices
            )
            window_directions.extend(direction_values)
            window_deltas.extend(
                [
                    list(map(float, row))
                    for row in cast(list[list[float]], _tensor_list(path_targets))
                ]
            )
            window_path_means.extend(
                [
                    list(map(float, row))
                    for row in cast(list[list[float]], _tensor_list(path_means))
                ]
            )
            window_path_scales.extend(
                [
                    list(map(float, row))
                    for row in cast(list[list[float]], _tensor_list(path_scales))
                ]
            )
            window_path_targets.extend(
                [
                    list(map(float, row))
                    for row in cast(list[list[float]], _tensor_list(path_targets))
                ]
            )
            window_indices.extend(int(index) for index in batch_row_indices)
    horizon_accuracy = {
        str(step + 1): round(
            sum(int(a == b) for a, b in zip(truth, predicted)) / max(1, len(truth)), 4
        )
        for step, (truth, predicted) in enumerate(zip(per_step_true, per_step_pred))
    }
    horizon_movement_accuracy = {
        str(step + 1): round(
            sum(int(a == b) for a, b in zip(truth, predicted)) / max(1, len(truth)), 4
        )
        for step, (truth, predicted) in enumerate(
            zip(per_step_movement_true, per_step_movement_pred)
        )
    }
    horizon_position_accuracy = {
        str(step + 1): round(
            sum(int(a == b) for a, b in zip(truth, predicted)) / max(1, len(truth)), 4
        )
        for step, (truth, predicted) in enumerate(
            zip(per_step_position_true, per_step_position_pred)
        )
    }
    majority_play = (
        max(set(play_true), key=play_true.count)
        if play_true
        else PLAY_TO_INDEX["PULLBACK"]
    )
    class_precision: dict[str, float] = {}
    for label, side in INDEX_TO_SIDE.items():
        predicted_count = sum(confusion[truth][label] for truth in (0, 1))
        class_precision[side] = (
            round(confusion[label][label] / predicted_count, 4)
            if predicted_count
            else 0.0
        )
    trajectory_mode_confusion = [
        [0 for _label in PATH_DIRECTION_LABELS] for _label in PATH_DIRECTION_LABELS
    ]
    for truth, predicted in zip(
        trajectory_mode_true_all,
        trajectory_mode_pred_all,
    ):
        trajectory_mode_confusion[truth][predicted] += 1
    trajectory_mode_brier = (
        sum(
            sum(
                (
                    float(probability)
                    - float(index == trajectory_mode_true_all[row_index])
                )
                ** 2
                for index, probability in enumerate(probabilities)
            )
            for row_index, probabilities in enumerate(trajectory_mode_probabilities_all)
        )
        / max(1, len(trajectory_mode_probabilities_all))
        if trajectory_mode_probabilities_all
        else None
    )
    metrics: dict[str, Any] = {
        "direction_accuracy": round(
            sum(int(a == b) for a, b in zip(true_all, pred_all))
            / max(1, len(true_all)),
            4,
        ),
        "balanced_accuracy": round(_balanced_accuracy(true_all, pred_all), 4),
        "natural_direction_accuracy": round(
            sum(int(a == b) for a, b in zip(true_all, natural_pred_all))
            / max(1, len(true_all)),
            4,
        ),
        "natural_balanced_accuracy": round(
            _balanced_accuracy(true_all, natural_pred_all), 4
        ),
        "predicted_class_precision": class_precision,
        "path_delta_mae": round(delta_error / max(1, delta_count), 6),
        "trajectory_target_mode_path_delta_mae": (
            round(target_mode_delta_error / target_mode_delta_count, 6)
            if target_mode_delta_count
            else None
        ),
        "trajectory_map_path_delta_mae": (
            round(delta_error / max(1, delta_count), 6)
            if trajectory_mode_true_all
            else None
        ),
        "trajectory_mode_accuracy": (
            round(
                sum(
                    int(truth == predicted)
                    for truth, predicted in zip(
                        trajectory_mode_true_all,
                        trajectory_mode_pred_all,
                    )
                )
                / max(1, len(trajectory_mode_true_all)),
                4,
            )
            if trajectory_mode_true_all
            else None
        ),
        "trajectory_mode_balanced_accuracy": (
            round(
                _multiclass_balanced_accuracy(
                    trajectory_mode_true_all,
                    trajectory_mode_pred_all,
                    len(PATH_DIRECTION_LABELS),
                ),
                4,
            )
            if trajectory_mode_true_all
            else None
        ),
        "trajectory_mode_brier": (
            round(trajectory_mode_brier, 6)
            if trajectory_mode_brier is not None
            else None
        ),
        "trajectory_mode_predicted_support": (
            {
                label: trajectory_mode_pred_all.count(index)
                for index, label in enumerate(PATH_DIRECTION_LABELS)
            }
            if trajectory_mode_true_all
            else None
        ),
        "trajectory_mode_confusion_matrix": (
            trajectory_mode_confusion if trajectory_mode_true_all else None
        ),
        "endpoint_path_direction_accuracy": round(
            endpoint_correct / max(1, endpoint_total), 4
        ),
        "endpoint_path_balanced_accuracy": round(
            _multiclass_balanced_accuracy(
                endpoint_true_all, endpoint_pred_all, len(PATH_DIRECTION_LABELS)
            ),
            4,
        ),
        "endpoint_path_persistence_accuracy": round(
            sum(
                int(a == b)
                for a, b in zip(endpoint_true_all, endpoint_persistence_pred)
            )
            / max(1, len(endpoint_true_all)),
            4,
        ),
        "endpoint_path_persistence_balanced_accuracy": round(
            _multiclass_balanced_accuracy(
                endpoint_true_all,
                endpoint_persistence_pred,
                len(PATH_DIRECTION_LABELS),
            ),
            4,
        ),
        "endpoint_predicted_support": {
            label: endpoint_pred_all.count(index)
            for index, label in enumerate(PATH_DIRECTION_LABELS)
        },
        "endpoint_source_cluster_accuracy_95": source_cluster_accuracy_interval(
            endpoint_true_all,
            endpoint_pred_all,
            endpoint_source_ids,
            samples=500,
            seed=84,
        ),
        "interval_90_coverage": round(
            covered / max(1, delta_count * len(PREDICTION_SCHEMA)), 4
        ),
        "path_interval_90_marginal_coverage": round(
            path_marginal_covered / max(1, delta_count), 4
        ),
        "path_movement_direction_accuracy": round(
            sum(int(a == b) for a, b in zip(movement_true_all, movement_pred_all))
            / max(1, len(movement_true_all)),
            4,
        ),
        "path_movement_balanced_accuracy": round(
            _multiclass_balanced_accuracy(
                movement_true_all, movement_pred_all, len(PATH_DIRECTION_LABELS)
            ),
            4,
        ),
        "horizon_position_balanced_accuracy": round(
            _multiclass_balanced_accuracy(
                position_true_all, position_pred_all, len(PATH_DIRECTION_LABELS)
            ),
            4,
        ),
        "play_accuracy": round(
            sum(int(a == b) for a, b in zip(play_true, play_pred))
            / max(1, len(play_true)),
            4,
        ),
        "play_balanced_accuracy": round(
            _multiclass_balanced_accuracy(play_true, play_pred, len(PLAY_LABELS)), 4
        ),
        "play_majority_baseline_accuracy": round(
            sum(int(value == majority_play) for value in play_true)
            / max(1, len(play_true)),
            4,
        ),
        "calibration": calibration_metrics(natural_probabilities_all, true_all),
        "calibration_error": round(
            sum(
                calibration_metrics(natural_probabilities_all, true_all)[
                    "classwise_ece"
                ].values()
            )
            / 2.0,
            4,
        ),
        "persistence_baseline_accuracy": round(
            persistence_correct / max(1, persistence_total), 4
        ),
        "persistence_baseline_balanced_accuracy": round(
            _balanced_accuracy(true_all, persistence_predictions), 4
        ),
        "horizon_direction_accuracy": horizon_accuracy,
        "horizon_path_movement_accuracy": horizon_movement_accuracy,
        "horizon_position_direction_accuracy": horizon_position_accuracy,
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
            "window_independent_groups": window_independent_groups,
            "window_directions": window_directions,
            "window_deltas": window_deltas,
            "window_path_means": window_path_means,
            "window_path_scales": window_path_scales,
            "window_path_targets": window_path_targets,
            "endpoint_labels": endpoint_true_all,
            "endpoint_decisions": endpoint_pred_all,
            "endpoint_source_ids": endpoint_source_ids,
            "trajectory_mode_labels": trajectory_mode_true_all,
            "trajectory_mode_decisions": trajectory_mode_pred_all,
            "trajectory_mode_probabilities": trajectory_mode_probabilities_all,
            "window_indices": window_indices,
        }
    return metrics


def _without_details(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "_details"}


def _path_model_selection_score(
    validation: Mapping[str, Any],
    *,
    horizon_steps: int,
) -> tuple[float, dict[str, float]]:
    """Select checkpoints for stable direct paths, never majority collapse."""

    endpoint_support = _mapping(validation.get("endpoint_predicted_support"))
    endpoint_total = max(
        1,
        sum(int(endpoint_support.get(side, 0) or 0) for side in PATH_DIRECTION_LABELS),
    )
    minimum_side_fraction = min(
        int(endpoint_support.get(side, 0) or 0) / endpoint_total
        for side in ("BUY", "SELL")
    )
    bidirectional_quality = min(1.0, minimum_side_fraction / 0.20)
    endpoint_accuracy = _finite_float(
        validation.get("endpoint_path_direction_accuracy")
    )
    endpoint_persistence = _finite_float(
        validation.get("endpoint_path_persistence_accuracy")
    )
    endpoint_margin = endpoint_accuracy - endpoint_persistence
    far_accuracy = _finite_float(
        _mapping(validation.get("horizon_path_movement_accuracy")).get(
            str(max(1, int(horizon_steps)))
        )
    )
    score = (
        0.28 * _finite_float(validation.get("path_movement_balanced_accuracy"))
        + 0.22 * _finite_float(validation.get("horizon_position_balanced_accuracy"))
        + 0.12 * far_accuracy
        + 0.20 * _finite_float(validation.get("endpoint_path_balanced_accuracy"))
        + 0.08 * endpoint_accuracy
        + 0.08 * bidirectional_quality
        + 0.04 * _finite_float(validation.get("balanced_accuracy"))
        + 0.30 * max(-0.25, min(0.25, endpoint_margin))
        - 0.20 * _finite_float(validation.get("path_delta_mae"), 1.0)
    )
    collapse_penalty = 0.20 if minimum_side_fraction < 0.05 else 0.0
    score -= collapse_penalty
    return score, {
        "minimum_buy_sell_fraction": round(minimum_side_fraction, 6),
        "bidirectional_quality": round(bidirectional_quality, 6),
        "endpoint_margin_over_persistence": round(endpoint_margin, 6),
        "collapse_penalty": collapse_penalty,
    }


def _pathwise_standardized_scores(
    details: Mapping[str, Any],
) -> tuple[list[float], dict[str, float]]:
    means = cast(Sequence[Sequence[Any]], details.get("window_path_means", []))
    scales = cast(Sequence[Sequence[Any]], details.get("window_path_scales", []))
    targets = cast(Sequence[Sequence[Any]], details.get("window_path_targets", []))
    sources = cast(
        Sequence[Any],
        details.get("window_independent_groups", details.get("window_sources", [])),
    )
    window_scores: list[float] = []
    source_scores: dict[str, float] = {}
    for index, (mean_row, scale_row, target_row) in enumerate(
        zip(means, scales, targets)
    ):
        horizon = min(len(mean_row), len(scale_row), len(target_row))
        if horizon <= 0:
            continue
        score = max(
            abs(_finite_float(target_row[step]) - _finite_float(mean_row[step]))
            / max(PATHWISE_CONFORMAL_SCALE_FLOOR, abs(_finite_float(scale_row[step])))
            for step in range(horizon)
        )
        source = (
            str(sources[index] if index < len(sources) else "").strip()
            or f"window:{index}"
        )
        window_scores.append(float(score))
        source_scores[source] = max(float(score), source_scores.get(source, 0.0))
    return window_scores, source_scores


def _finite_sample_conformal_quantile(
    scores: Sequence[float], alpha: float
) -> tuple[float, int]:
    clean: list[float] = []
    for value in scores:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            clean.append(number)
    clean.sort()
    if not clean:
        return 0.0, 0
    bounded_alpha = min(0.999999, max(1e-6, float(alpha)))
    rank = min(
        len(clean), max(1, int(math.ceil((len(clean) + 1) * (1.0 - bounded_alpha))))
    )
    return float(clean[rank - 1]), rank


def _fit_source_grouped_pathwise_conformal(
    validation_details: Mapping[str, Any],
    *,
    alpha: float = PATHWISE_CONFORMAL_ALPHA,
) -> dict[str, Any]:
    """Fit one simultaneous path band score per independent perceptual group.

    Multiple causal windows and near-duplicate screenshots are correlated.  We
    therefore take the worst trajectory score for each manifest perceptual
    group before applying the finite-sample split-conformal quantile.  The
    calibrated band covers all horizons together rather than reporting
    misleading pointwise coverage.
    """

    window_scores, source_scores = _pathwise_standardized_scores(validation_details)
    quantile, rank = _finite_sample_conformal_quantile(
        list(source_scores.values()), alpha
    )
    return {
        "schema_version": "PG_PERCEPTUAL_GROUP_PATHWISE_CONFORMAL_V3",
        "target_coverage": round(1.0 - float(alpha), 6),
        "alpha": round(float(alpha), 6),
        "calibration_unit": "PERCEPTUAL_SCREENSHOT_GROUP",
        "nonconformity_score": "GROUP_MAX_OF_PATH_MAX_ABS_ERROR_DIV_MODEL_SCALE",
        "band_semantics": "ANCHOR_PLUS_DIRECT_DISPLACEMENT_MEAN_PLUS_OR_MINUS_QUANTILE_TIMES_SCALE",
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "scale_floor": PATHWISE_CONFORMAL_SCALE_FLOOR,
        "calibration_windows": len(window_scores),
        # Compatibility name retained for older metrics readers.  The unit is
        # now explicitly an independent perceptual group, not an image count.
        "calibration_sources": len(source_scores),
        "calibration_independent_groups": len(source_scores),
        "finite_sample_rank": rank,
        "quantile": round(quantile, 8),
    }


def _evaluate_pathwise_conformal(
    details: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> dict[str, Any]:
    quantile = max(0.0, _finite_float(calibration.get("quantile")))
    window_scores, source_scores = _pathwise_standardized_scores(details)
    window_coverage = sum(score <= quantile for score in window_scores) / max(
        1, len(window_scores)
    )
    source_coverage = sum(score <= quantile for score in source_scores.values()) / max(
        1, len(source_scores)
    )

    scales = cast(Sequence[Sequence[Any]], details.get("window_path_scales", []))
    targets = cast(Sequence[Sequence[Any]], details.get("window_path_targets", []))
    means = cast(Sequence[Sequence[Any]], details.get("window_path_means", []))
    horizon_total: list[int] = []
    horizon_covered: list[int] = []
    widths: list[float] = []
    for mean_row, scale_row, target_row in zip(means, scales, targets):
        horizon = min(len(mean_row), len(scale_row), len(target_row))
        while len(horizon_total) < horizon:
            horizon_total.append(0)
            horizon_covered.append(0)
        for step in range(horizon):
            scale = max(
                PATHWISE_CONFORMAL_SCALE_FLOOR, abs(_finite_float(scale_row[step]))
            )
            error = abs(_finite_float(target_row[step]) - _finite_float(mean_row[step]))
            horizon_total[step] += 1
            horizon_covered[step] += int(error <= quantile * scale)
            widths.append(2.0 * quantile * scale * MAX_PRICE_DELTA)
    per_horizon = {
        str(step + 1): round(horizon_covered[step] / max(1, total), 4)
        for step, total in enumerate(horizon_total)
    }
    marginal_total = sum(horizon_total)
    marginal_covered = sum(horizon_covered)
    return {
        "target_coverage": calibration.get("target_coverage"),
        "quantile": round(quantile, 8),
        "trajectory_simultaneous_coverage": round(window_coverage, 4),
        "source_simultaneous_coverage": round(source_coverage, 4),
        "marginal_horizon_coverage": round(
            marginal_covered / max(1, marginal_total), 4
        ),
        "per_horizon_coverage": per_horizon,
        "mean_full_band_width_relative_price": round(
            sum(widths) / max(1, len(widths)), 6
        ),
        "evaluated_windows": len(window_scores),
        "evaluated_sources": len(source_scores),
    }


def _path_probability_rows(
    details: Mapping[str, Any],
    step: int,
) -> dict[str, Any]:
    means = cast(Sequence[Sequence[Any]], details.get("window_path_means", []))
    scales = cast(Sequence[Sequence[Any]], details.get("window_path_scales", []))
    targets = cast(Sequence[Sequence[Any]], details.get("window_path_targets", []))
    sources = cast(
        Sequence[Any],
        details.get("window_independent_groups", details.get("window_sources", [])),
    )
    logits: list[list[float]] = []
    labels: list[int] = []
    decisions: list[int] = []
    source_ids: list[str] = []
    for index, (mean_row, scale_row, target_row) in enumerate(
        zip(means, scales, targets)
    ):
        if step >= min(len(mean_row), len(scale_row), len(target_row)):
            continue
        target = _finite_float(target_row[step])
        if abs(target) <= PATH_DIRECTION_HOLD_THRESHOLD_SCALED:
            continue
        mean = _finite_float(mean_row[step])
        scale = max(PATHWISE_CONFORMAL_SCALE_FLOOR, abs(_finite_float(scale_row[step])))
        standardized = max(-12.0, min(12.0, mean / scale))
        logits.append([standardized, -standardized])
        labels.append(SIDE_TO_INDEX["BUY"] if target > 0.0 else SIDE_TO_INDEX["SELL"])
        decisions.append(SIDE_TO_INDEX["BUY"] if mean >= 0.0 else SIDE_TO_INDEX["SELL"])
        source_ids.append(str(sources[index] if index < len(sources) else ""))
    return {
        "logits": logits,
        "labels": labels,
        "decisions": decisions,
        "source_ids": source_ids,
    }


def _fit_path_probability_controls(
    validation_details: Mapping[str, Any],
    test_details: Mapping[str, Any],
    *,
    horizon_steps: int,
    target_precision: float,
    minimum_predictions: int,
) -> dict[str, Any]:
    horizons: dict[str, Any] = {}
    for step in range(max(1, int(horizon_steps))):
        validation = _path_probability_rows(validation_details, step)
        test = _path_probability_rows(test_details, step)
        validation_labels = cast(list[int], validation["labels"])
        if len(validation_labels) < 20 or any(
            validation_labels.count(label) < 5 for label in (0, 1)
        ):
            horizons[str(step + 1)] = {
                "probability_calibrated": False,
                "reason": "insufficient_validation_buy_sell_support",
            }
            continue
        temperature = fit_temperature(
            cast(Sequence[Sequence[float]], validation["logits"]),
            validation_labels,
        )
        validation_probabilities = temperature_softmax(
            cast(Sequence[Sequence[float]], validation["logits"]),
            temperature,
        )
        selection = choose_class_conditional_thresholds(
            validation_probabilities,
            validation_labels,
            cast(Sequence[int], validation["decisions"]),
            target_precision=target_precision,
            minimum_predictions=minimum_predictions,
        )
        thresholds = dict(cast(Mapping[str, Any], selection.get("thresholds", {})))
        test_probabilities = temperature_softmax(
            cast(Sequence[Sequence[float]], test["logits"]),
            temperature,
        )
        test_selection = evaluate_class_conditional_selection(
            test_probabilities,
            cast(Sequence[int], test["labels"]),
            cast(Sequence[int], test["decisions"]),
            thresholds,
        )
        horizons[str(step + 1)] = {
            "probability_calibrated": True,
            "temperature": round(float(temperature), 6),
            "thresholds": thresholds,
            "validation_selection": selection,
            "test_selection": test_selection,
            "validation_calibration": calibration_metrics(
                validation_probabilities,
                validation_labels,
            ),
            "test_calibration": calibration_metrics(
                test_probabilities,
                cast(Sequence[int], test["labels"]),
            ),
            "validation_support": len(validation_labels),
            "test_support": len(cast(Sequence[int], test["labels"])),
        }
    first = _mapping(horizons.get("1"))
    endpoint = _mapping(horizons.get(str(max(1, int(horizon_steps)))))
    return {
        "schema_version": "PG_PATH_PROBABILITY_CALIBRATION_V3",
        "target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "score": "STANDARDIZED_DIRECT_DISPLACEMENT_TWO_LOGIT_TEMPERATURE_SCALE",
        "hold_threshold_scaled": PATH_DIRECTION_HOLD_THRESHOLD_SCALED,
        "horizons": horizons,
        "probability_calibrated": bool(
            first.get("probability_calibrated") is True
            and endpoint.get("probability_calibrated") is True
        ),
        "first_horizon": "1",
        "endpoint_horizon": str(max(1, int(horizon_steps))),
    }


def _build_train_retrieval_bank(
    train_rows: Sequence[Mapping[str, Any]],
    details: Mapping[str, Any],
) -> dict[str, Any]:
    indices = [
        int(value) for value in cast(Sequence[Any], details.get("window_indices", []))
    ]
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
        entry_ids=[
            str(train_rows[index].get("window_id") or f"train-{index}")
            for index in indices
        ],
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
        for row in cast(
            Sequence[Sequence[float]], details.get("decision_probabilities", [])
        )
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
                [
                    float(probability_map.get("BUY", 0.5)),
                    float(probability_map.get("SELL", 0.5)),
                ]
            )
            retrieval_confidences.append(
                float(horizon.get("effective_confidence", 0.0) or 0.0)
            )
    blend = max(0.0, min(0.75, float(alpha)))
    probabilities: list[list[float]] = []
    decisions: list[int] = []
    for index, model_row in enumerate(model_probabilities):
        retrieval_row = (
            retrieval_probabilities[index]
            if index < len(retrieval_probabilities)
            else [0.5, 0.5]
        )
        support = (
            retrieval_confidences[index] if index < len(retrieval_confidences) else 0.0
        )
        effective_alpha = blend * max(0.0, min(1.0, support))
        probability = [
            (1.0 - effective_alpha) * model_row[class_index]
            + effective_alpha * retrieval_row[class_index]
            for class_index in range(2)
        ]
        probabilities.append(probability)
        decision_row = (
            decision_probabilities[index]
            if index < len(decision_probabilities)
            else model_row
        )
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
        "source_ids": [
            str(value) for value in cast(Sequence[Any], details.get("source_ids", []))
        ],
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
            accuracy = sum(
                int(left == right) for left, right in zip(labels, decisions)
            ) / max(1, len(labels))
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
    return best or {
        "top_k": 1,
        "alpha": 0.0,
        "validation_accuracy": 0.0,
        "validation_balanced_accuracy": 0.0,
        "score": 0.0,
    }


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
    retrieval = _choose_retrieval_settings(
        validation_details, bank, temperature=temperature
    )
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
        selected_mask.append(
            float(probabilities[int(decision)]) >= float(thresholds.get(side, 1.01))
        )
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
    parser = argparse.ArgumentParser(
        description="Upgrade the PhoenixGuard V3 computer-vision LSTM path model from raw 808 Memory suites."
    )
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
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=1)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument(
        "--trajectory-modes",
        type=int,
        choices=(0, len(PATH_DIRECTION_LABELS)),
        default=len(PATH_DIRECTION_LABELS),
        help=(
            "0 keeps the old unconditional path head; 3 enables the V3 "
            "BUY/SELL/HOLD endpoint-conditioned trajectory decoder"
        ),
    )
    parser.add_argument("--epochs", type=int, default=28)
    parser.add_argument("--patience", type=int, default=7)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=8e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--target-selective-precision", type=float, default=0.85)
    parser.add_argument("--minimum-selective-predictions", type=int, default=20)
    parser.add_argument(
        "--checkpoint-path", type=Path, default=DEFAULT_TRAINING_CHECKPOINT
    )
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
                "feature_events": sum(
                    len(cast(Sequence[Any], row.get("features", [])))
                    for row in sequences
                ),
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
    print(
        json.dumps({"stage": "causal_windows_ready", "windows": len(windows)}),
        flush=True,
    )
    train_rows, validation_rows, test_rows = _split_rows(windows)
    usable_source_paths = {str(row.get("source") or "") for row in windows}
    usable_sequences = [
        row
        for row in sequences
        if str(row.get("source_path") or row.get("source") or "") in usable_source_paths
    ]
    image_source_counts = {
        split: len({str(row.get("source")) for row in subset})
        for split, subset in (
            ("train", train_rows),
            ("validation", validation_rows),
            ("test", test_rows),
        )
    }
    source_counts = {
        split: len(
            {str(row.get("independent_group") or row.get("source")) for row in subset}
        )
        for split, subset in (
            ("train", train_rows),
            ("validation", validation_rows),
            ("test", test_rows),
        )
    }
    if not train_rows or not validation_rows or not test_rows:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "source_grouped_train_val_test_rows_required",
                    "windows": len(windows),
                    "sources": source_counts,
                },
                indent=2,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "stage": "source_splits_ready",
                "independent_groups": source_counts,
                "source_images": image_source_counts,
                "training_windows": len(train_rows),
                "validation_windows": len(validation_rows),
                "test_windows": len(test_rows),
            }
        ),
        flush=True,
    )
    prewarm_started = time.time()
    chart_context_prewarm = _prewarm_chart_context_cache(
        [*train_rows, *validation_rows, *test_rows]
    )
    chart_context_prewarm["seconds"] = round(time.time() - prewarm_started, 3)
    print(
        json.dumps(
            {
                "stage": "chart_contexts_ready",
                **chart_context_prewarm,
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
        trajectory_modes=int(args.trajectory_modes),
    )
    print(
        json.dumps(
            {
                "stage": "model_ready",
                "parameters": sum(
                    parameter.numel() for parameter in model.parameters()
                ),
            }
        ),
        flush=True,
    )
    optimizer: torch.optim.Optimizer = torch.optim.AdamW(
        model.parameters(), lr=float(args.lr), weight_decay=1e-3
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="max", factor=0.55, patience=3, min_lr=1e-5
    )
    train_loader = DataLoader(
        CandlePathDataset(train_rows),
        batch_size=int(args.batch_size),
        shuffle=True,
    )
    class_weights = _class_weights(train_rows)
    play_class_weights = _play_class_weights(train_rows)
    trajectory_mode_class_weights = _trajectory_mode_class_weights(train_rows)
    history: list[dict[str, Any]] = []
    best_state: dict[str, Any] | None = None
    best_score = -1e9
    stale_epochs = 0
    start_epoch = 1
    if bool(args.resume) and args.checkpoint_path.exists():
        checkpoint = cast(
            Mapping[str, Any],
            torch.load(args.checkpoint_path, map_location="cpu", weights_only=False),
        )
        if (
            str(checkpoint.get("training_target_schema_version") or "")
            != TRAINING_TARGET_SCHEMA_VERSION
            or str(checkpoint.get("training_recipe_schema_version") or "")
            != TRAINING_RECIPE_SCHEMA_VERSION
            or int(checkpoint.get("trajectory_modes", 0) or 0)
            != int(args.trajectory_modes)
        ):
            print(
                json.dumps(
                    {
                        "stage": "training_checkpoint_ignored",
                        "reason": "target_semantics_changed",
                        "required_target_schema": TRAINING_TARGET_SCHEMA_VERSION,
                        "required_recipe_schema": TRAINING_RECIPE_SCHEMA_VERSION,
                    }
                ),
                flush=True,
            )
        else:
            model.load_state_dict(cast(Mapping[str, Any], checkpoint["model_state"]))
            optimizer.load_state_dict(
                cast(dict[str, Any], checkpoint["optimizer_state"])
            )
            scheduler.load_state_dict(
                cast(dict[str, Any], checkpoint["scheduler_state"])
            )
            stored_best = checkpoint.get("best_state")
            best_state = (
                dict(cast(Mapping[str, Any], stored_best))
                if isinstance(stored_best, Mapping)
                else None
            )
            best_score = float(checkpoint.get("best_score", best_score))
            stale_epochs = int(checkpoint.get("stale_epochs", 0))
            history = [
                dict(cast(Mapping[str, Any], row))
                for row in cast(Sequence[Any], checkpoint.get("history", []))
                if isinstance(row, Mapping)
            ]
            start_epoch = int(checkpoint.get("epoch", 0)) + 1
            print(
                json.dumps(
                    {
                        "stage": "training_resumed",
                        "start_epoch": start_epoch,
                        "best_score": best_score,
                    }
                ),
                flush=True,
            )
    for epoch in range(start_epoch, int(args.epochs) + 1):
        epoch_started = time.time()
        model.train()
        total_loss = 0.0
        batches = 0
        for (
            sequence,
            targets,
            directions,
            plays,
            lengths,
            chart_context,
            target_quality,
            _row_indices,
        ) in train_loader:
            sequence, chart_context = _augment_training_batch(
                sequence,
                chart_context,
                lengths,
            )
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
                trajectory_mode_class_weights=trajectory_mode_class_weights,
            )
            cast(Callable[[], Any], getattr(loss, "backward"))()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            cast(Callable[[], Any], getattr(optimizer, "step"))()
            total_loss += float(loss.item())
            batches += 1
        validation = evaluate(model, validation_rows, int(args.batch_size))
        score, selection_evidence = _path_model_selection_score(
            validation,
            horizon_steps=int(args.horizon_steps),
        )
        cast(Callable[[float], None], getattr(scheduler, "step"))(score)
        improved = score > best_score + 1e-5
        if improved:
            best_score = score
            best_state = {
                key: value.detach().cpu().clone()
                for key, value in model.state_dict().items()
            }
            stale_epochs = 0
        else:
            stale_epochs += 1
        epoch_row = {
            "epoch": epoch,
            "epoch_seconds": round(time.time() - epoch_started, 3),
            "loss": round(total_loss / max(1, batches), 6),
            "decoder_mode": "DIRECT_HORIZON_QUERIES",
            "learning_rate": round(float(optimizer.param_groups[0]["lr"]), 8),
            "model_selection_score": round(score, 8),
            "model_selection_evidence": selection_evidence,
            **validation,
        }
        history.append(epoch_row)
        args.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_torch_save(
            {
                "schema_version": "PG_LSTM_CANDLE_PATH_TRAINING_CHECKPOINT_V3",
                "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
                "training_target_schema_version": TRAINING_TARGET_SCHEMA_VERSION,
                "training_recipe_schema_version": TRAINING_RECIPE_SCHEMA_VERSION,
                "trajectory_modes": int(args.trajectory_modes),
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
    train_evaluation = evaluate(
        model, train_rows, int(args.batch_size), return_details=True
    )
    validation_evaluation = evaluate(
        model, validation_rows, int(args.batch_size), return_details=True
    )
    test_evaluation = evaluate(
        model, test_rows, int(args.batch_size), return_details=True
    )
    train_details = _mapping(train_evaluation.get("_details"))
    validation_details = _mapping(validation_evaluation.get("_details"))
    test_details = _mapping(test_evaluation.get("_details"))
    pathwise_conformal = _fit_source_grouped_pathwise_conformal(validation_details)
    validation_evaluation["pathwise_conformal"] = _evaluate_pathwise_conformal(
        validation_details,
        pathwise_conformal,
    )
    test_evaluation["pathwise_conformal"] = _evaluate_pathwise_conformal(
        test_details,
        pathwise_conformal,
    )
    validation_evaluation["pathwise_conformal_quantile"] = pathwise_conformal.get(
        "quantile"
    )
    test_evaluation["pathwise_conformal_quantile"] = pathwise_conformal.get("quantile")
    retrieval_bank = _build_train_retrieval_bank(train_rows, train_details)
    risk_control = _calibrate_and_select(
        validation_details,
        test_details,
        retrieval_bank,
        target_precision=float(args.target_selective_precision),
        minimum_predictions=int(args.minimum_selective_predictions),
    )
    risk_control["pathwise_conformal"] = pathwise_conformal
    path_probability_controls = _fit_path_probability_controls(
        validation_details,
        test_details,
        horizon_steps=int(args.horizon_steps),
        target_precision=float(args.target_selective_precision),
        minimum_predictions=int(args.minimum_selective_predictions),
    )
    risk_control["trajectory"] = path_probability_controls
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
    artifact_generation_id = hashlib.sha256(
        (
            f"{LSTM_CANDLE_SEQUENCE_VERSION}|{time.time_ns()}|"
            f"{args.seed}|{len(train_rows)}|{best_score:.12f}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    artifact_payload: dict[str, Any] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_ARTIFACT_V3",
        "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
        "artifact_generation_id": artifact_generation_id,
        "training_target_schema_version": TRAINING_TARGET_SCHEMA_VERSION,
        "training_recipe_schema_version": TRAINING_RECIPE_SCHEMA_VERSION,
        "trajectory_modes": int(args.trajectory_modes),
        "state_dict": model.state_dict(),
        "feature_schema": list(FEATURE_SCHEMA),
        "prediction_schema": list(PREDICTION_SCHEMA),
        "index_to_side": INDEX_TO_SIDE,
        "play_to_index": PLAY_TO_INDEX,
        "retrieval_bank": retrieval_bank,
        "risk_control": risk_control,
        "pathwise_conformal": pathwise_conformal,
    }
    config: dict[str, Any] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONFIG_V3",
        "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
        "artifact_generation_id": artifact_generation_id,
        "stack_version": "PHOENIXGUARD_V3",
        "modality": "COMPUTER_VISION",
        "training_source": "RAW_SCREENSHOT_SUITES",
        "architecture": DIRECT_RAW_CV_ARCHITECTURE,
        "visual_frontend": "SHARED_ADAPTIVE_PALETTE_OHLC_PLUS_CAUSAL_RAW_CHART_PIXELS",
        "extractor_schema_version": EXTRACTOR_SCHEMA_VERSION,
        "training_target_schema_version": TRAINING_TARGET_SCHEMA_VERSION,
        "training_recipe_schema_version": TRAINING_RECIPE_SCHEMA_VERSION,
        "feature_schema": list(FEATURE_SCHEMA),
        "prediction_schema": list(PREDICTION_SCHEMA),
        "prediction_schema_semantics": {
            "body_norm": "DIRECT_HORIZON_CANDLE_BODY_HEIGHT",
            "upper_wick_norm": "DIRECT_HORIZON_CANDLE_UPPER_WICK_HEIGHT",
            "lower_wick_norm": "DIRECT_HORIZON_CANDLE_LOWER_WICK_HEIGHT",
            "range_norm": "DIRECT_HORIZON_CANDLE_HIGH_LOW_RANGE",
            PATH_TARGET_FEATURE: "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        },
        "path_target_feature": PATH_TARGET_FEATURE,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "path_output_is_incremental": False,
        "path_reconstruction": "OBSERVED_ANCHOR_PLUS_DIRECT_HORIZON_DISPLACEMENT",
        "path_target_scale": MAX_PRICE_DELTA,
        "path_target_clipped_range": [-1.0, 1.0],
        "direction_head_target": "CANDLE_BODY_COLOR_BUY_SELL",
        "direction_target_semantics": "CANDLE_BODY_COLOR_BUY_SELL",
        "horizon_position_target": "SIGN_OF_DIRECT_CUMULATIVE_CLOSE_DISPLACEMENT_FROM_ANCHOR",
        "movement_direction_target": "SIGN_OF_EVENT_DELTA_BETWEEN_DIRECT_HORIZON_CLOSES",
        "movement_direction_labels": list(PATH_DIRECTION_LABELS),
        "movement_direction_hold_threshold_scaled": PATH_DIRECTION_HOLD_THRESHOLD_SCALED,
        "movement_direction_source": "REGRESSION_PATH_CHANNEL",
        "trajectory_modes": int(args.trajectory_modes),
        "trajectory_mode_labels": list(PATH_DIRECTION_LABELS),
        "trajectory_mode_target": (
            "FINAL_DIRECT_CUMULATIVE_CLOSE_DISPLACEMENT_BUY_SELL_HOLD"
            if int(args.trajectory_modes)
            else "DISABLED"
        ),
        "trajectory_mode_decoder": (
            "ENDPOINT_CLASSIFIER_PLUS_PER_MODE_DIRECT_CUMULATIVE_MEAN_SCALE"
            if int(args.trajectory_modes)
            else "UNCONDITIONAL_DIRECT_CUMULATIVE_MEAN_SCALE"
        ),
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
        "image_source_counts": image_source_counts,
        "probability_temperature": risk_control.get("temperature"),
        "selective_direction_thresholds": risk_control.get("thresholds"),
        "selective_direction_threshold_semantics": "CANDLE_BODY_COLOR_BUY_SELL",
        "retrieval": risk_control.get("retrieval"),
        "pathwise_conformal": pathwise_conformal,
        "path_probability_calibration": path_probability_controls,
        "pathwise_conformal_quantile": pathwise_conformal.get("quantile"),
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
        "artifact_generation_id": artifact_generation_id,
        "stack_version": "PHOENIXGUARD_V3",
        "modality": "COMPUTER_VISION",
        "training_target_schema_version": TRAINING_TARGET_SCHEMA_VERSION,
        "training_recipe_schema_version": TRAINING_RECIPE_SCHEMA_VERSION,
        "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
        "trajectory_modes": int(args.trajectory_modes),
        "trajectory_mode_labels": list(PATH_DIRECTION_LABELS),
        "direction_target_semantics": "CANDLE_BODY_COLOR_BUY_SELL",
        "horizon_position_target": "SIGN_OF_DIRECT_CUMULATIVE_CLOSE_DISPLACEMENT_FROM_ANCHOR_WITH_HOLD_ZONE",
        "movement_direction_target": "SIGN_OF_EVENT_DELTA_BETWEEN_DIRECT_HORIZON_CLOSES_WITH_HOLD_ZONE",
        "pathwise_conformal": pathwise_conformal,
        "path_probability_calibration": path_probability_controls,
        "production_ready": production_ready,
        "point_estimate_selective_85": point_estimate_selective_85,
        "a_grade_selective_85": a_grade_selective_85,
        "production_readiness": readiness,
        "source_images": len(sequences),
        "usable_source_images": len(usable_sequences),
        "source_counts": source_counts,
        "image_source_counts": image_source_counts,
        "training_windows": len(train_rows),
        "validation_windows": len(validation_rows),
        "test_windows": len(test_rows),
        "validation": validation_metrics,
        "test": test_metrics,
        "risk_control": risk_control,
        "selective_target_precision": float(args.target_selective_precision),
        "test_selective_accuracy": test_selection.get("accuracy"),
        "test_selective_coverage": test_selection.get("coverage"),
        "test_selective_macro_precision": test_selection.get(
            "macro_predicted_class_precision"
        ),
        "test_selective_buy_precision": buy_selection.get("precision"),
        "test_selective_sell_precision": sell_selection.get("precision"),
        "test_selective_source_cluster_accuracy_95": risk_control.get(
            "test_selected_source_cluster_accuracy_95"
        ),
        **{f"test_{key}": value for key, value in test_metrics.items()},
        "training_seconds": round(time.time() - started, 3),
        "history": history,
    }
    _publish_artifact_bundle(
        artifact=artifact_payload,
        config=config,
        metrics=metrics,
        model_path=args.model_path,
        config_path=args.config_path,
        metrics_path=args.metrics_path,
    )
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
                "image_source_counts": image_source_counts,
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
