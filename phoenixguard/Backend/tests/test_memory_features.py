from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.memory.memory_features import late_interaction_score  # noqa: E402


def _reference_late_interaction_score(
    query_tokens: Sequence[Sequence[float]] | None,
    candidate_tokens: Sequence[Sequence[float]] | None,
) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0

    def cosine(left_values: Sequence[float], right_values: Sequence[float]) -> float:
        left = np.asarray(list(left_values), dtype=np.float32).reshape(-1)
        right = np.asarray(list(right_values), dtype=np.float32).reshape(-1)
        if left.size == 0 or right.size == 0:
            return 0.0
        dim = min(int(left.size), int(right.size))
        left = left[:dim]
        right = right[:dim]
        left_norm = float(np.linalg.norm(left))
        right_norm = float(np.linalg.norm(right))
        if left_norm <= 1e-8 or right_norm <= 1e-8:
            return 0.0
        return float(np.clip(np.dot(left / left_norm, right / right_norm), -1.0, 1.0))

    best_scores = [
        max(
            0.0,
            max(
                (cosine(query, candidate) for candidate in candidate_tokens),
                default=0.0,
            ),
        )
        for query in query_tokens
    ]
    return float(np.clip(np.mean(np.asarray(best_scores, dtype=np.float32)), 0.0, 1.0))


def test_late_interaction_score_matches_scalar_reference_for_dense_tokens() -> None:
    shapes = [
        ((5, 32), (5, 32)),
        ((3, 7), (4, 5)),
        ((1, 1), (6, 9)),
    ]
    rng = np.random.default_rng(808)
    for query_shape, candidate_shape in shapes:
        query_tokens = rng.normal(size=query_shape).astype(np.float32).tolist()
        candidate_tokens = rng.normal(size=candidate_shape).astype(np.float32).tolist()

        actual = late_interaction_score(query_tokens, candidate_tokens)
        expected = _reference_late_interaction_score(query_tokens, candidate_tokens)

        assert abs(actual - expected) <= 1e-6


def test_late_interaction_score_matches_scalar_reference_for_ragged_tokens() -> None:
    query_tokens = [[1.0, 0.5, -0.25, 0.75], [0.0, 1.0], [0.2, -0.4, 0.8]]
    candidate_tokens = [[0.9, 0.4, -0.2], [-0.8, -0.1, 0.6, 0.3, 0.2], [0.0]]

    actual = late_interaction_score(query_tokens, candidate_tokens)
    expected = _reference_late_interaction_score(query_tokens, candidate_tokens)

    assert abs(actual - expected) <= 1e-6


def test_late_interaction_score_preserves_empty_zero_and_clipping_edges() -> None:
    assert late_interaction_score(None, [[1.0, 0.0]]) == 0.0
    assert late_interaction_score([], [[1.0, 0.0]]) == 0.0
    assert late_interaction_score([[1.0, 0.0]], []) == 0.0
    assert late_interaction_score([[]], [[1.0, 0.0]]) == 0.0
    assert late_interaction_score([[0.0, 0.0]], [[1.0, 0.0]]) == 0.0
    assert late_interaction_score([[1.0, 0.0]], [[-1.0, 0.0]]) == 0.0
    assert abs(late_interaction_score([[1.0, 0.0]], [[1.0, 0.0]]) - 1.0) <= 1e-7
