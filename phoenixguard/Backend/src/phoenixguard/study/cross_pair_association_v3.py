"""Bounded closed-timestamp cross-pair association studies for V3.

The implementation compares an autoregressive baseline with an augmented
lagged-source regression and combines its variance-reduction proxy with
discrete mutual information.  A deterministic circular-shift max-lag test
controls lag selection.  Results are explicitly associations, never causal
claims, forecasts, or execution evidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
from typing import Any, cast


CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION = "PG_CROSS_PAIR_ASSOCIATION_V3"
MAX_CROSS_PAIR_SAMPLES = 1_024
MAX_CROSS_PAIR_LAG = 12
MAX_NULL_CIRCULAR_SHIFTS = 255
MAX_CROSS_PAIR_GRAPH_PAIRS = 8
MAX_CROSS_PAIR_GRAPH_EDGES = 64
CROSS_PAIR_COMPATIBLE_COORDINATE_SPACES = frozenset(
    {
        "NORMALIZED_RETURN",
        "NORMALIZED_MEDIAN_RANGE",
        "NORMALIZED_PRICE_PATH",
        "STANDARDIZED_FEATURE",
    }
)


class CrossPairAssociationValidationError(ValueError):
    """Raised when cross-pair evidence is incompatible or non-causal."""


def _identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise CrossPairAssociationValidationError(f"{field} is required")
    if len(text) > maximum:
        raise CrossPairAssociationValidationError(
            f"{field} exceeds {maximum} characters"
        )
    return text


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise CrossPairAssociationValidationError(f"{field} must be finite")
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CrossPairAssociationValidationError(
            f"{field} must be finite"
        ) from exc
    if not math.isfinite(numeric):
        raise CrossPairAssociationValidationError(f"{field} must be finite")
    return numeric


def _integer(value: object, *, field: str, minimum: int, maximum: int) -> int:
    numeric = _finite(value, field=field)
    if not numeric.is_integer() or numeric < minimum or numeric > maximum:
        raise CrossPairAssociationValidationError(
            f"{field} must be an integer in [{minimum}, {maximum}]"
        )
    return int(numeric)


def _safety_contract() -> dict[str, Any]:
    return {
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "predictive_probability": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


def _canonical_series(
    value: object,
    *,
    field: str,
    maximum: int,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise CrossPairAssociationValidationError(f"{field} must be a sequence")
    source_rows = list(cast(Sequence[object], value))
    if len(source_rows) < 8 or len(source_rows) > maximum:
        raise CrossPairAssociationValidationError(
            f"{field} must contain between 8 and {maximum} rows"
        )
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(source_rows):
        if not isinstance(raw, Mapping):
            raise CrossPairAssociationValidationError(
                f"{field}[{index}] must be a mapping"
            )
        row = dict(cast(Mapping[str, Any], raw))
        if row.get("is_closed") is not True:
            raise CrossPairAssociationValidationError(
                f"{field}[{index}] is not a completed closed candle"
            )
        rows.append(
            {
                "pair_id": _identity(
                    row.get("pair_id"),
                    field=f"{field}[{index}].pair_id",
                    maximum=96,
                ),
                "candle_id": _identity(
                    row.get("candle_id"),
                    field=f"{field}[{index}].candle_id",
                    maximum=256,
                ),
                "closed_timestamp": _finite(
                    row.get("closed_timestamp"),
                    field=f"{field}[{index}].closed_timestamp",
                ),
                "coordinate_space": _identity(
                    row.get("coordinate_space"),
                    field=f"{field}[{index}].coordinate_space",
                    maximum=64,
                ),
                "order_domain": _identity(
                    row.get("order_domain"),
                    field=f"{field}[{index}].order_domain",
                    maximum=64,
                ),
                "value": _finite(
                    row.get("value"), field=f"{field}[{index}].value"
                ),
            }
        )
    pair_ids = {str(row["pair_id"]) for row in rows}
    spaces = {str(row["coordinate_space"]) for row in rows}
    order_domains = {str(row["order_domain"]) for row in rows}
    if len(pair_ids) != 1:
        raise CrossPairAssociationValidationError(
            f"{field} must belong to exactly one pair"
        )
    if len(spaces) != 1:
        raise CrossPairAssociationValidationError(
            f"{field} mixes coordinate spaces"
        )
    if len(order_domains) != 1:
        raise CrossPairAssociationValidationError(f"{field} mixes order domains")
    timestamps = [float(row["closed_timestamp"]) for row in rows]
    candle_ids = [str(row["candle_id"]) for row in rows]
    if len(set(timestamps)) != len(timestamps):
        raise CrossPairAssociationValidationError(
            f"{field} contains duplicate closed timestamps"
        )
    if len(set(candle_ids)) != len(candle_ids):
        raise CrossPairAssociationValidationError(
            f"{field} contains duplicate candle identities"
        )
    if any(timestamps[index] <= timestamps[index - 1] for index in range(1, len(timestamps))):
        raise CrossPairAssociationValidationError(
            f"{field} must be strictly ordered by closed_timestamp"
        )
    intervals = [
        timestamps[index] - timestamps[index - 1]
        for index in range(1, len(timestamps))
    ]
    reference_interval = intervals[0]
    if reference_interval <= 0.0 or any(
        not math.isclose(
            interval,
            reference_interval,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
        for interval in intervals[1:]
    ):
        raise CrossPairAssociationValidationError(
            f"{field} must be a contiguous uniform closed-timestamp series"
        )
    return (
        next(iter(pair_ids)),
        next(iter(spaces)),
        next(iter(order_domains)),
        rows,
    )


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[index][:] + [vector[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-15:
            continue
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if abs(factor) <= 1e-18:
                continue
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def _ridge_sse(design: Sequence[Sequence[float]], target: Sequence[float]) -> float:
    if not design or not target or len(design) != len(target):
        return 0.0
    column_count = len(design[0])
    gram = [[0.0] * column_count for _ in range(column_count)]
    cross = [0.0] * column_count
    for row, outcome in zip(design, target, strict=True):
        if len(row) != column_count:
            raise CrossPairAssociationValidationError(
                "internal regression design is ragged"
            )
        for left in range(column_count):
            cross[left] += float(row[left]) * float(outcome)
            for right in range(column_count):
                gram[left][right] += float(row[left]) * float(row[right])
    # A tiny deterministic ridge stabilizes collinear lag matrices.  The
    # intercept is not regularized.
    scale = max(1.0, sum(abs(gram[index][index]) for index in range(column_count)))
    ridge = 1e-10 * scale
    for index in range(1, column_count):
        gram[index][index] += ridge
    coefficients = _solve_linear_system(gram, cross)
    return sum(
        (
            float(outcome)
            - sum(coefficient * float(value) for coefficient, value in zip(coefficients, row, strict=True))
        )
        ** 2
        for row, outcome in zip(design, target, strict=True)
    )


def _discrete_bins(values: Sequence[float], requested_bins: int) -> list[int]:
    low = min(values)
    high = max(values)
    if math.isclose(low, high, rel_tol=0.0, abs_tol=1e-15):
        return [0] * len(values)
    scale = requested_bins / (high - low)
    return [
        min(requested_bins - 1, max(0, int((value - low) * scale)))
        for value in values
    ]


def _mutual_information(
    left: Sequence[float],
    right: Sequence[float],
    *,
    requested_bins: int,
) -> tuple[float, float]:
    if len(left) != len(right) or not left:
        return 0.0, 0.0
    bins = min(requested_bins, max(2, int(math.sqrt(len(left)))))
    left_bins = _discrete_bins(left, bins)
    right_bins = _discrete_bins(right, bins)
    joint = Counter(zip(left_bins, right_bins, strict=True))
    left_counts = Counter(left_bins)
    right_counts = Counter(right_bins)
    total = len(left_bins)
    information = 0.0
    for (left_bin, right_bin), count in joint.items():
        probability = count / total
        information += probability * math.log(
            probability
            / ((left_counts[left_bin] / total) * (right_counts[right_bin] / total))
        )
    left_entropy = -sum(
        (count / total) * math.log(count / total) for count in left_counts.values()
    )
    right_entropy = -sum(
        (count / total) * math.log(count / total) for count in right_counts.values()
    )
    denominator = min(left_entropy, right_entropy)
    normalized = information / denominator if denominator > 1e-15 else 0.0
    return max(0.0, information), max(0.0, min(1.0, normalized))


def _lag_candidate(
    source: Sequence[float],
    target: Sequence[float],
    *,
    lag: int,
    requested_bins: int,
) -> dict[str, Any]:
    outcomes: list[float] = []
    source_lagged: list[float] = []
    baseline_design: list[list[float]] = []
    augmented_design: list[list[float]] = []
    for index in range(lag, len(target)):
        target_lags = [float(target[index - offset]) for offset in range(1, lag + 1)]
        source_lags = [float(source[index - offset]) for offset in range(1, lag + 1)]
        outcomes.append(float(target[index]))
        source_lagged.append(float(source[index - lag]))
        baseline_design.append([1.0, *target_lags])
        augmented_design.append([1.0, *target_lags, *source_lags])
    baseline_sse = _ridge_sse(baseline_design, outcomes)
    augmented_sse = _ridge_sse(augmented_design, outcomes)
    reduction = (
        max(0.0, min(1.0, (baseline_sse - augmented_sse) / baseline_sse))
        if baseline_sse > 1e-15
        else 0.0
    )
    information, normalized_information = _mutual_information(
        source_lagged,
        outcomes,
        requested_bins=requested_bins,
    )
    score = 0.60 * reduction + 0.40 * normalized_information
    return {
        "lag_completed_candles": lag,
        "support": len(outcomes),
        "baseline_residual_sum_squares": round(baseline_sse, 10),
        "augmented_residual_sum_squares": round(augmented_sse, 10),
        "granger_style_variance_reduction": round(reduction, 10),
        "mutual_information_nats": round(information, 10),
        "normalized_mutual_information": round(normalized_information, 10),
        "association_score": round(score, 10),
    }


def _circular_offsets(length: int, maximum: int) -> list[int]:
    available = length - 1
    count = min(maximum, available)
    if count == available:
        return list(range(1, length))
    offsets = {
        max(1, min(available, round(index * length / (count + 1))))
        for index in range(1, count + 1)
    }
    candidate = 1
    while len(offsets) < count:
        offsets.add(candidate)
        candidate += 1
    return sorted(offsets)[:count]


def _direction_study(
    source: Sequence[float],
    target: Sequence[float],
    *,
    max_lag: int,
    bins: int,
    max_null_shifts: int,
) -> dict[str, Any]:
    candidates = [
        _lag_candidate(source, target, lag=lag, requested_bins=bins)
        for lag in range(1, max_lag + 1)
    ]
    best = max(
        candidates,
        key=lambda row: (
            float(row["association_score"]),
            -int(row["lag_completed_candles"]),
        ),
    )
    observed_score = float(best["association_score"])
    null_maxima: list[float] = []
    for offset in _circular_offsets(len(source), max_null_shifts):
        shifted = list(source[offset:]) + list(source[:offset])
        null_maxima.append(
            max(
                float(
                    _lag_candidate(
                        shifted,
                        target,
                        lag=lag,
                        requested_bins=bins,
                    )["association_score"]
                )
                for lag in range(1, max_lag + 1)
            )
        )
    exceedances = sum(
        value >= observed_score - 1e-12 for value in null_maxima
    )
    empirical_p = (1 + exceedances) / (1 + len(null_maxima))
    return {
        "best": best,
        "empirical_max_lag_p_value": round(empirical_p, 10),
        "null_shift_count": len(null_maxima),
        "tested_lag_count": len(candidates),
    }


def analyze_cross_pair_lead_lag_v3(
    left_series: Sequence[Mapping[str, Any]],
    right_series: Sequence[Mapping[str, Any]],
    *,
    max_lag: int = 6,
    minimum_support: int = 32,
    significance_alpha: float = 0.05,
    mutual_information_bins: int = 8,
    max_samples: int = MAX_CROSS_PAIR_SAMPLES,
    max_null_shifts: int = 127,
) -> dict[str, Any]:
    """Return only significant non-causal lead-lag associations.

    Both streams must be distinct pairs with the exact same contiguous closed
    timestamps, normalized coordinate space, and order domain.  Lag selection
    is tested against deterministic circular-shift nulls; Bonferroni correction
    covers the two tested directions.
    """

    bounded_samples = _integer(
        max_samples,
        field="max_samples",
        minimum=32,
        maximum=MAX_CROSS_PAIR_SAMPLES,
    )
    bounded_lag = _integer(
        max_lag,
        field="max_lag",
        minimum=1,
        maximum=MAX_CROSS_PAIR_LAG,
    )
    support_floor = _integer(
        minimum_support,
        field="minimum_support",
        minimum=16,
        maximum=MAX_CROSS_PAIR_SAMPLES,
    )
    bins = _integer(
        mutual_information_bins,
        field="mutual_information_bins",
        minimum=2,
        maximum=32,
    )
    null_shifts = _integer(
        max_null_shifts,
        field="max_null_shifts",
        minimum=15,
        maximum=MAX_NULL_CIRCULAR_SHIFTS,
    )
    alpha = _finite(significance_alpha, field="significance_alpha")
    if not 0.0 < alpha <= 0.10:
        raise CrossPairAssociationValidationError(
            "significance_alpha must be in (0, 0.10]"
        )
    left_pair, left_space, left_order, left_rows = _canonical_series(
        left_series,
        field="left_series",
        maximum=bounded_samples,
    )
    right_pair, right_space, right_order, right_rows = _canonical_series(
        right_series,
        field="right_series",
        maximum=bounded_samples,
    )
    if left_pair == right_pair:
        raise CrossPairAssociationValidationError(
            "cross-pair study requires two distinct pair identities"
        )
    if left_space != right_space:
        raise CrossPairAssociationValidationError(
            "cross-pair series must use the exact same coordinate space"
        )
    if left_space not in CROSS_PAIR_COMPATIBLE_COORDINATE_SPACES:
        raise CrossPairAssociationValidationError(
            "cross-pair series require an explicitly normalized compatible "
            "coordinate space"
        )
    if left_order != right_order:
        raise CrossPairAssociationValidationError(
            "cross-pair series must use the exact same order domain"
        )
    left_timestamps = [float(row["closed_timestamp"]) for row in left_rows]
    right_timestamps = [float(row["closed_timestamp"]) for row in right_rows]
    if left_timestamps != right_timestamps:
        raise CrossPairAssociationValidationError(
            "cross-pair series must share every exact closed timestamp"
        )
    if len(left_rows) - bounded_lag < support_floor:
        raise CrossPairAssociationValidationError(
            "aligned closed-candle support is below minimum_support after lagging"
        )

    left_values = [float(row["value"]) for row in left_rows]
    right_values = [float(row["value"]) for row in right_rows]
    directional = [
        (
            left_pair,
            right_pair,
            _direction_study(
                left_values,
                right_values,
                max_lag=bounded_lag,
                bins=bins,
                max_null_shifts=null_shifts,
            ),
        ),
        (
            right_pair,
            left_pair,
            _direction_study(
                right_values,
                left_values,
                max_lag=bounded_lag,
                bins=bins,
                max_null_shifts=null_shifts,
            ),
        ),
    ]
    evidence_core = {
        "left_candle_ids": [row["candle_id"] for row in left_rows],
        "right_candle_ids": [row["candle_id"] for row in right_rows],
        "closed_timestamps": left_timestamps,
        "coordinate_space": left_space,
        "order_domain": left_order,
    }
    evidence_digest = hashlib.sha256(
        json.dumps(
            evidence_core,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    significant: list[dict[str, Any]] = []
    for source_pair, target_pair, study in directional:
        adjusted_p = min(
            1.0, 2.0 * float(study["empirical_max_lag_p_value"])
        )
        if adjusted_p > alpha:
            continue
        significant.append(
            {
                "source_pair_id": source_pair,
                "target_pair_id": target_pair,
                **cast(dict[str, Any], study["best"]),
                "empirical_max_lag_p_value": study[
                    "empirical_max_lag_p_value"
                ],
                "bonferroni_adjusted_p_value": round(adjusted_p, 10),
                "null_shift_count": study["null_shift_count"],
                "coordinate_space": left_space,
                "order_domain": left_order,
                "evidence_digest": evidence_digest,
                "interpretation": (
                    "Lagged historical association only; Granger-style variance "
                    "reduction and mutual information do not establish causation."
                ),
                **_safety_contract(),
            }
        )
    significant.sort(
        key=lambda row: (
            float(row["bonferroni_adjusted_p_value"]),
            -float(row["association_score"]),
            str(row["source_pair_id"]),
        )
    )
    return {
        "schema_version": CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
        "status": "SUPPORTED" if significant else "NO_SIGNIFICANT_ASSOCIATION",
        "analysis_kind": (
            "BOUNDED_GRANGER_STYLE_PROXY_AND_MUTUAL_INFORMATION_ASSOCIATION"
        ),
        "aligned_closed_candle_count": len(left_rows),
        "closed_timestamp_start": left_timestamps[0],
        "closed_timestamp_end": left_timestamps[-1],
        "coordinate_space": left_space,
        "order_domain": left_order,
        "tested_direction_count": 2,
        "tested_lag_count_per_direction": bounded_lag,
        "significance_alpha": alpha,
        "multiple_testing_correction": "BONFERRONI_TWO_DIRECTIONS",
        "significant_associations": significant,
        "suppressed_non_significant_direction_count": 2 - len(significant),
        "evidence_digest": evidence_digest,
        "contract": {
            "publishes_only_significant_associations": True,
            "requires_exact_shared_closed_timestamps": True,
            "requires_exact_compatible_coordinate_space": True,
            "requires_exact_order_domain": True,
            "granger_style_is_proxy_not_causal_test": True,
            **_safety_contract(),
        },
        **_safety_contract(),
    }


def build_cross_pair_association_graph_v3(
    series_by_pair: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    max_lag: int = 6,
    minimum_support: int = 32,
    significance_alpha: float = 0.05,
    mutual_information_bins: int = 8,
    max_samples: int = MAX_CROSS_PAIR_SAMPLES,
    max_null_shifts: int = 127,
    max_pairs: int = MAX_CROSS_PAIR_GRAPH_PAIRS,
    max_edges: int = MAX_CROSS_PAIR_GRAPH_EDGES,
) -> dict[str, Any]:
    """Build a bounded graph from all deterministic pairwise studies.

    Significance is controlled inside each unordered pair family across its two
    possible directions.  The graph contract discloses that scope instead of
    implying a global causal network.
    """

    pair_limit = _integer(
        max_pairs,
        field="max_pairs",
        minimum=2,
        maximum=MAX_CROSS_PAIR_GRAPH_PAIRS,
    )
    edge_limit = _integer(
        max_edges,
        field="max_edges",
        minimum=1,
        maximum=MAX_CROSS_PAIR_GRAPH_EDGES,
    )
    raw_mapping = dict(series_by_pair)
    if len(raw_mapping) < 2 or len(raw_mapping) > pair_limit:
        raise CrossPairAssociationValidationError(
            f"series_by_pair must contain between 2 and {pair_limit} pairs"
        )
    canonical: dict[str, Sequence[Mapping[str, Any]]] = {}
    for raw_pair_id, series in raw_mapping.items():
        pair_id = _identity(raw_pair_id, field="series_by_pair key", maximum=96)
        if pair_id in canonical:
            raise CrossPairAssociationValidationError(
                "series_by_pair contains duplicate canonical pair identities"
            )
        canonical[pair_id] = series

    pair_ids = sorted(canonical)
    edges: list[dict[str, Any]] = []
    pair_study_digests: list[str] = []
    unordered_pair_count = 0
    for left_index, left_pair in enumerate(pair_ids):
        for right_pair in pair_ids[left_index + 1 :]:
            study = analyze_cross_pair_lead_lag_v3(
                canonical[left_pair],
                canonical[right_pair],
                max_lag=max_lag,
                minimum_support=minimum_support,
                significance_alpha=significance_alpha,
                mutual_information_bins=mutual_information_bins,
                max_samples=max_samples,
                max_null_shifts=max_null_shifts,
            )
            unordered_pair_count += 1
            pair_study_digests.append(str(study["evidence_digest"]))
            edges.extend(
                deepcopy(cast(list[dict[str, Any]], study["significant_associations"]))
            )
    edges.sort(
        key=lambda row: (
            float(row["bonferroni_adjusted_p_value"]),
            -float(row["association_score"]),
            str(row["source_pair_id"]),
            str(row["target_pair_id"]),
        )
    )
    significant_edge_count = len(edges)
    published_edges = edges[:edge_limit]
    graph_digest = hashlib.sha256(
        json.dumps(
            {
                "pair_ids": pair_ids,
                "pair_study_digests": pair_study_digests,
                "published_edge_bindings": [
                    {
                        "source": row["source_pair_id"],
                        "target": row["target_pair_id"],
                        "lag": row["lag_completed_candles"],
                        "evidence_digest": row["evidence_digest"],
                    }
                    for row in published_edges
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION,
        "status": "SUPPORTED" if published_edges else "NO_SIGNIFICANT_ASSOCIATION",
        "analysis_kind": "BOUNDED_MULTI_PAIR_ASSOCIATION_GRAPH",
        "nodes": [
            {
                "pair_id": pair_id,
                "study_only": True,
                "execution_authority": False,
            }
            for pair_id in pair_ids
        ],
        "edges": published_edges,
        "unordered_pair_study_count": unordered_pair_count,
        "tested_direction_count": 2 * unordered_pair_count,
        "significant_edge_count_before_bound": significant_edge_count,
        "published_edge_count": len(published_edges),
        "edges_truncated_by_bound": significant_edge_count > edge_limit,
        "graph_digest": graph_digest,
        "contract": {
            "significance_scope": "BONFERRONI_WITHIN_EACH_PAIR_TWO_DIRECTIONS",
            "global_network_causation_claimed": False,
            "publishes_only_significant_associations": True,
            "maximum_pairs": pair_limit,
            "maximum_edges": edge_limit,
            **_safety_contract(),
        },
        **_safety_contract(),
    }


__all__ = [
    "CROSS_PAIR_ASSOCIATION_SCHEMA_VERSION",
    "CROSS_PAIR_COMPATIBLE_COORDINATE_SPACES",
    "MAX_CROSS_PAIR_LAG",
    "MAX_CROSS_PAIR_GRAPH_EDGES",
    "MAX_CROSS_PAIR_GRAPH_PAIRS",
    "MAX_CROSS_PAIR_SAMPLES",
    "MAX_NULL_CIRCULAR_SHIFTS",
    "CrossPairAssociationValidationError",
    "analyze_cross_pair_lead_lag_v3",
    "build_cross_pair_association_graph_v3",
]
