"""Leakage-safe retrieval-augmented multi-horizon forecasting for V3.

The bank format is deliberately JSON-serializable.  Every entry carries its
split membership and validation fails closed unless every entry belongs to the
training split.  Retrieval uses normalized context embeddings and collapses
matches by source before selecting neighbors, so repeated windows from one
chart cannot overwhelm independent evidence.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray


BANK_SCHEMA_V3 = "PG_RETRIEVAL_FORECAST_BANK_V3"
MODEL_VERSION_V3 = "retrieval_forecast_v3"
TRAIN_SPLIT = "train"
SIDES = ("SELL", "BUY")


def _to_numpy(value: Any, *, name: str) -> NDArray[Any]:
    """Convert NumPy, Torch, or sequence input without importing Torch."""

    candidate = value
    if hasattr(candidate, "detach"):
        candidate = candidate.detach()
    if hasattr(candidate, "cpu"):
        candidate = candidate.cpu()
    if hasattr(candidate, "numpy"):
        candidate = candidate.numpy()
    try:
        return np.asarray(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} cannot be converted to an array") from exc


def _finite_float_matrix(value: Any, *, name: str) -> NDArray[np.float64]:
    try:
        matrix = _to_numpy(value, name=name).astype(np.float64, copy=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only numeric values") from exc
    if matrix.ndim != 2:
        raise ValueError(f"{name} must have shape [entries, features]")
    if not np.isfinite(matrix).all():
        raise ValueError(f"{name} must contain only finite values")
    return matrix


def _canonical_side(value: Any) -> str:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bool):
        return "BUY" if bool(value) else "SELL"
    if isinstance(value, int) and int(value) in (0, 1):
        return SIDES[int(value)]
    if isinstance(value, float) and float(value) in (0.0, 1.0):
        return SIDES[int(value)]
    side = str(value).strip().upper()
    if side in {"0", "1"}:
        return SIDES[int(side)]
    if side not in SIDES:
        raise ValueError(f"direction labels must be BUY/SELL or 1/0, got {value!r}")
    return side


def _canonical_direction_rows(value: Any, *, entries: int) -> list[list[str]]:
    labels = _to_numpy(value, name="next_directions")
    if labels.ndim != 2:
        raise ValueError("next_directions must have shape [entries, horizon]")
    if labels.shape[0] != entries:
        raise ValueError("next_directions entry count does not match embeddings")
    if labels.shape[1] <= 0:
        raise ValueError("forecast horizon must be positive")
    return [[_canonical_side(item) for item in row] for row in labels.tolist()]


def _normalize_rows(
    matrix: NDArray[np.float64],
    *,
    name: str,
) -> NDArray[np.float64]:
    if matrix.shape[1] <= 0:
        raise ValueError(f"{name} feature dimension must be positive")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms <= 1e-12):
        raise ValueError(f"{name} cannot contain zero-length vectors")
    return matrix / norms[:, None]


def _string_list(values: Sequence[Any], *, name: str, entries: int) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a sequence with one value per entry")
    result = [str(value).strip() for value in values]
    if len(result) != entries:
        raise ValueError(f"{name} entry count does not match embeddings")
    if any(not value for value in result):
        raise ValueError(f"{name} values cannot be empty")
    return result


def build_retrieval_bank_v3(
    context_embeddings: Any,
    source_ids: Sequence[Any],
    next_directions: Any,
    continuous_targets: Any,
    *,
    split_labels: Sequence[Any],
    entry_ids: Sequence[Any] | None = None,
    embedding_dim: int | None = None,
    horizon: int | None = None,
) -> dict[str, Any]:
    """Build a normalized, JSON-serializable bank from training data only.

    ``split_labels`` is required instead of inferred.  This makes the caller
    prove the provenance of every row and prevents validation/test rows from
    entering the retrieval bank through a permissive default.

    Empty banks are supported when ``embedding_dim`` and ``horizon`` are
    supplied explicitly.
    """

    requested_dimension = int(embedding_dim) if embedding_dim is not None else None
    requested_horizon = int(horizon) if horizon is not None else None
    raw_embeddings = _to_numpy(context_embeddings, name="context_embeddings")
    if raw_embeddings.ndim == 1 and raw_embeddings.size == 0:
        embeddings = np.empty((0, requested_dimension or 0), dtype=np.float64)
    else:
        embeddings = _finite_float_matrix(
            raw_embeddings,
            name="context_embeddings",
        )
    entries = int(embeddings.shape[0])

    if entries == 0:
        if requested_dimension is None or requested_dimension <= 0:
            raise ValueError(
                "embedding_dim must be positive when building an empty bank"
            )
        if requested_horizon is None or requested_horizon <= 0:
            raise ValueError("horizon must be positive when building an empty bank")
        if embeddings.shape[1] not in (0, requested_dimension):
            raise ValueError("empty embedding shape conflicts with embedding_dim")
        if len(source_ids) or len(split_labels):
            raise ValueError(
                "empty embeddings require empty source_ids and split_labels"
            )
        if _to_numpy(next_directions, name="next_directions").size:
            raise ValueError("empty embeddings require empty next_directions")
        if _to_numpy(continuous_targets, name="continuous_targets").size:
            raise ValueError("empty embeddings require empty continuous_targets")
        if entry_ids is not None and len(entry_ids):
            raise ValueError("empty embeddings require empty entry_ids")
        return {
            "schema": BANK_SCHEMA_V3,
            "model_version": MODEL_VERSION_V3,
            "split_policy": "train_only",
            "side_encoding": {"SELL": 0, "BUY": 1},
            "embedding_dim": requested_dimension,
            "horizon": requested_horizon,
            "entries": [],
        }

    normalized = _normalize_rows(embeddings, name="context_embeddings")
    dimension = int(normalized.shape[1])
    if requested_dimension is not None and requested_dimension != dimension:
        raise ValueError("embedding_dim does not match context_embeddings")

    sources = _string_list(source_ids, name="source_ids", entries=entries)
    splits = [
        value.lower()
        for value in _string_list(split_labels, name="split_labels", entries=entries)
    ]
    if any(value != TRAIN_SPLIT for value in splits):
        raise ValueError(
            "retrieval bank leakage: every entry must be from the train split"
        )

    directions = _canonical_direction_rows(next_directions, entries=entries)
    detected_horizon = len(directions[0])
    if requested_horizon is not None and requested_horizon != detected_horizon:
        raise ValueError("horizon does not match next_directions")

    targets = _finite_float_matrix(continuous_targets, name="continuous_targets")
    if targets.shape != (entries, detected_horizon):
        raise ValueError("continuous_targets must match [entries, horizon]")

    if entry_ids is None:
        identifiers = [f"train-{index:08d}" for index in range(entries)]
    else:
        identifiers = _string_list(entry_ids, name="entry_ids", entries=entries)
    if len(set(identifiers)) != entries:
        raise ValueError("entry_ids must be unique")

    bank_entries: list[dict[str, Any]] = []
    for index in range(entries):
        bank_entries.append(
            {
                "entry_id": identifiers[index],
                "source_id": sources[index],
                "split": TRAIN_SPLIT,
                "context_embedding": normalized[index].tolist(),
                "next_directions": directions[index],
                "continuous_targets": targets[index].tolist(),
            }
        )
    bank: dict[str, Any] = {
        "schema": BANK_SCHEMA_V3,
        "model_version": MODEL_VERSION_V3,
        "split_policy": "train_only",
        "side_encoding": {"SELL": 0, "BUY": 1},
        "embedding_dim": dimension,
        "horizon": detected_horizon,
        "entries": bank_entries,
    }
    return validate_retrieval_bank_v3(bank)


def validate_retrieval_bank_v3(bank: object) -> dict[str, Any]:
    """Validate a serialized bank and return a canonical detached copy."""

    if not isinstance(bank, Mapping):
        raise ValueError("retrieval bank must be a mapping")
    bank_mapping = cast(Mapping[str, Any], bank)
    if bank_mapping.get("schema") != BANK_SCHEMA_V3:
        raise ValueError(f"retrieval bank schema must be {BANK_SCHEMA_V3}")
    if bank_mapping.get("model_version") != MODEL_VERSION_V3:
        raise ValueError(f"retrieval bank model_version must be {MODEL_VERSION_V3}")
    if bank_mapping.get("split_policy") != "train_only":
        raise ValueError("retrieval bank split_policy must be train_only")
    raw_dimension = bank_mapping.get("embedding_dim")
    raw_horizon = bank_mapping.get("horizon")
    try:
        dimension = int(cast(Any, raw_dimension))
        horizon = int(cast(Any, raw_horizon))
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding_dim and horizon must be integers") from exc
    if dimension <= 0 or horizon <= 0:
        raise ValueError("embedding_dim and horizon must be positive")

    raw_entries_value: object = bank_mapping.get("entries")
    if not isinstance(raw_entries_value, list):
        raise ValueError("retrieval bank entries must be a list")
    raw_entries = cast(list[object], raw_entries_value)

    canonical_entries: list[dict[str, Any]] = []
    seen_entry_ids: set[str] = set()
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping):
            raise ValueError(f"bank entry {index} must be a mapping")
        entry = cast(Mapping[str, Any], raw_entry)
        if str(entry.get("split", "")).strip().lower() != TRAIN_SPLIT:
            raise ValueError(
                f"retrieval bank leakage: entry {index} is not train split"
            )

        entry_id = str(entry.get("entry_id", "")).strip()
        source_id = str(entry.get("source_id", "")).strip()
        if not entry_id or not source_id:
            raise ValueError(f"bank entry {index} requires entry_id and source_id")
        if entry_id in seen_entry_ids:
            raise ValueError(f"duplicate entry_id in retrieval bank: {entry_id}")
        seen_entry_ids.add(entry_id)

        embedding = _to_numpy(
            entry.get("context_embedding"),
            name=f"entries[{index}].context_embedding",
        )
        try:
            embedding = embedding.astype(np.float64, copy=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bank entry {index} embedding must be numeric") from exc
        if embedding.ndim != 1 or embedding.shape[0] != dimension:
            raise ValueError(f"bank entry {index} embedding dimension mismatch")
        if not np.isfinite(embedding).all():
            raise ValueError(f"bank entry {index} embedding must be finite")
        norm = float(np.linalg.norm(embedding))
        if not math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError(f"bank entry {index} embedding must be normalized")

        direction_values_value: object = entry.get("next_directions")
        if not isinstance(direction_values_value, Sequence) or isinstance(
            direction_values_value, (str, bytes)
        ):
            raise ValueError(f"bank entry {index} next_directions must be a sequence")
        direction_values = cast(Sequence[Any], direction_values_value)
        directions = [_canonical_side(value) for value in direction_values]
        if len(directions) != horizon:
            raise ValueError(f"bank entry {index} direction horizon mismatch")

        target = _to_numpy(
            entry.get("continuous_targets"),
            name=f"entries[{index}].continuous_targets",
        )
        try:
            target = target.astype(np.float64, copy=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"bank entry {index} continuous_targets must be numeric"
            ) from exc
        if target.ndim != 1 or target.shape[0] != horizon:
            raise ValueError(f"bank entry {index} continuous target horizon mismatch")
        if not np.isfinite(target).all():
            raise ValueError(f"bank entry {index} continuous_targets must be finite")

        canonical_entries.append(
            {
                "entry_id": entry_id,
                "source_id": source_id,
                "split": TRAIN_SPLIT,
                "context_embedding": embedding.tolist(),
                "next_directions": directions,
                "continuous_targets": target.tolist(),
            }
        )

    return {
        "schema": BANK_SCHEMA_V3,
        "model_version": MODEL_VERSION_V3,
        "split_policy": "train_only",
        "side_encoding": {"SELL": 0, "BUY": 1},
        "embedding_dim": dimension,
        "horizon": horizon,
        "entries": canonical_entries,
    }


def _fallback_forecast(
    *, query_index: int, horizon: int, status: str
) -> dict[str, Any]:
    return {
        "query_index": query_index,
        "status": status,
        "neighbor_count": 0,
        "unique_source_count": 0,
        "effective_sample_size": 0.0,
        "mean_similarity": 0.0,
        "effective_confidence": 0.0,
        "horizons": [
            {
                "step": step + 1,
                "probabilities": {"BUY": 0.5, "SELL": 0.5},
                "predicted_side": "TIE",
                "continuous_mean": None,
                "continuous_uncertainty": None,
                "effective_confidence": 0.0,
            }
            for step in range(horizon)
        ],
        "neighbors": [],
    }


def retrieve_forecast_v3(
    bank: Mapping[str, Any],
    query_embeddings: Any,
    *,
    top_k: int = 8,
    minimum_similarity: float = 0.0,
    similarity_power: float = 1.0,
) -> list[dict[str, Any]]:
    """Return one deterministic forecast per query embedding.

    Cosine matches are ordered by descending similarity, then stable entry ID,
    then bank index.  Only the strongest match from each source can survive.
    Similarities above ``minimum_similarity`` are remapped to [0, 1] and used
    as non-negative weights.  The bank is revalidated on every call so a
    tampered validation/test entry fails before retrieval.
    """

    canonical = validate_retrieval_bank_v3(bank)
    count = int(top_k)
    if count <= 0:
        raise ValueError("top_k must be positive")
    floor = float(minimum_similarity)
    power = float(similarity_power)
    if not math.isfinite(floor) or floor < -1.0 or floor >= 1.0:
        raise ValueError("minimum_similarity must be finite and in [-1, 1)")
    if not math.isfinite(power) or power <= 0.0:
        raise ValueError("similarity_power must be finite and positive")

    raw_queries = _to_numpy(query_embeddings, name="query_embeddings")
    try:
        queries = raw_queries.astype(np.float64, copy=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("query_embeddings must contain only numeric values") from exc
    if queries.ndim == 1:
        queries = queries[None, :]
    if queries.ndim != 2 or queries.shape[1] != canonical["embedding_dim"]:
        raise ValueError("query_embeddings must have shape [queries, embedding_dim]")
    if not np.isfinite(queries).all():
        raise ValueError("query_embeddings must contain only finite values")
    normalized_queries = _normalize_rows(queries, name="query_embeddings")

    entries = cast(list[dict[str, Any]], canonical["entries"])
    horizon = int(canonical["horizon"])
    if not entries:
        return [
            _fallback_forecast(query_index=index, horizon=horizon, status="empty_bank")
            for index in range(normalized_queries.shape[0])
        ]

    bank_embeddings = np.asarray(
        [entry["context_embedding"] for entry in entries],
        dtype=np.float64,
    )
    similarities = normalized_queries @ bank_embeddings.T
    forecasts: list[dict[str, Any]] = []

    for query_index, similarity_row in enumerate(similarities):
        ranked = sorted(
            range(len(entries)),
            key=lambda index: (
                -float(similarity_row[index]),
                str(entries[index]["entry_id"]),
                index,
            ),
        )
        selected_indices: list[int] = []
        seen_sources: set[str] = set()
        for index in ranked:
            similarity = float(np.clip(similarity_row[index], -1.0, 1.0))
            if similarity <= floor:
                continue
            source_id = str(entries[index]["source_id"])
            if source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            selected_indices.append(index)
            if len(selected_indices) == count:
                break

        if not selected_indices:
            forecasts.append(
                _fallback_forecast(
                    query_index=query_index,
                    horizon=horizon,
                    status="no_eligible_neighbors",
                )
            )
            continue

        selected_similarities = np.asarray(
            [
                float(np.clip(similarity_row[index], -1.0, 1.0))
                for index in selected_indices
            ],
            dtype=np.float64,
        )
        remapped = np.clip(
            (selected_similarities - floor) / max(1e-12, 1.0 - floor),
            0.0,
            1.0,
        )
        raw_weights = np.power(remapped, power)
        weight_sum = float(raw_weights.sum())
        if weight_sum <= 1e-12:
            forecasts.append(
                _fallback_forecast(
                    query_index=query_index,
                    horizon=horizon,
                    status="no_eligible_neighbors",
                )
            )
            continue
        weights = raw_weights / weight_sum
        effective_sample_size = float(1.0 / np.square(weights).sum())
        mean_similarity = float(np.dot(weights, selected_similarities))
        support_confidence = 1.0 - math.exp(-effective_sample_size / 3.0)

        horizon_rows: list[dict[str, Any]] = []
        horizon_confidences: list[float] = []
        for step in range(horizon):
            buy_values = np.asarray(
                [
                    float(entries[index]["next_directions"][step] == "BUY")
                    for index in selected_indices
                ],
                dtype=np.float64,
            )
            target_values = np.asarray(
                [
                    float(entries[index]["continuous_targets"][step])
                    for index in selected_indices
                ],
                dtype=np.float64,
            )
            buy_probability = float(np.dot(weights, buy_values))
            sell_probability = 1.0 - buy_probability
            target_mean = float(np.dot(weights, target_values))
            target_variance = float(
                np.dot(weights, np.square(target_values - target_mean))
            )
            directional_certainty = 2.0 * abs(buy_probability - 0.5)
            step_confidence = float(
                np.clip(
                    max(0.0, mean_similarity)
                    * directional_certainty
                    * support_confidence,
                    0.0,
                    1.0,
                )
            )
            horizon_confidences.append(step_confidence)
            predicted_side = "TIE"
            if buy_probability > 0.5:
                predicted_side = "BUY"
            elif sell_probability > 0.5:
                predicted_side = "SELL"
            horizon_rows.append(
                {
                    "step": step + 1,
                    "probabilities": {
                        "BUY": buy_probability,
                        "SELL": sell_probability,
                    },
                    "predicted_side": predicted_side,
                    "continuous_mean": target_mean,
                    "continuous_uncertainty": math.sqrt(max(0.0, target_variance)),
                    "effective_confidence": step_confidence,
                }
            )

        neighbors: list[dict[str, Any]] = []
        for rank, (index, similarity, weight) in enumerate(
            zip(selected_indices, selected_similarities, weights, strict=True),
            start=1,
        ):
            entry = entries[index]
            neighbors.append(
                {
                    "rank": rank,
                    "bank_index": index,
                    "entry_id": entry["entry_id"],
                    "source_id": entry["source_id"],
                    "similarity": float(similarity),
                    "weight": float(weight),
                    "next_directions": list(entry["next_directions"]),
                    "continuous_targets": list(entry["continuous_targets"]),
                }
            )

        forecasts.append(
            {
                "query_index": query_index,
                "status": "ok",
                "neighbor_count": len(neighbors),
                "unique_source_count": len(seen_sources),
                "effective_sample_size": effective_sample_size,
                "mean_similarity": mean_similarity,
                "effective_confidence": float(np.mean(horizon_confidences)),
                "horizons": horizon_rows,
                "neighbors": neighbors,
            }
        )
    return forecasts


__all__ = [
    "BANK_SCHEMA_V3",
    "MODEL_VERSION_V3",
    "SIDES",
    "TRAIN_SPLIT",
    "build_retrieval_bank_v3",
    "retrieve_forecast_v3",
    "validate_retrieval_bank_v3",
]
