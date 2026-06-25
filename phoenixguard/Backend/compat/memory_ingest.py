from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from _pg_bootstrap import ensure_project_paths

ensure_project_paths()

from phoenixguard.memory import memory_ingest as _impl
from phoenixguard.memory.memory_ingest import (
    EMBED_DIM,
    SHARED_DIM,
    VISUAL_DIM,
    MemoryBank,
    MemoryEntry,
    MemoryIngestor,
    RecallResult,
)


class HNSWIndexCompat(Protocol):
    def __init__(self, dim: int = SHARED_DIM) -> None:
        ...

    def build(self, entries: list[MemoryEntry], logger: logging.Logger | None = None) -> None:
        ...

    def search(self, query: NDArray[np.float32], top_k: int = 5) -> list[tuple[str, float]]:
        ...

    def save(self, path: Path) -> None:
        ...

    def load(self, path: Path, n_entries: int) -> None:
        ...


_HNSWIndex = cast(type[HNSWIndexCompat], getattr(_impl, "_HNSWIndex"))


def _chart_state_to_text(chart_state: dict[str, Any]) -> str:
    impl = cast(Callable[[dict[str, Any]], str], getattr(_impl, "_chart_state_to_text"))
    return impl(chart_state)


def _dual_encode(text_embed: NDArray[np.float32], visual_fp: NDArray[np.float32]) -> NDArray[np.float32]:
    impl = cast(
        Callable[[NDArray[np.float32], NDArray[np.float32]], NDArray[np.float32]],
        getattr(_impl, "_dual_encode"),
    )
    return impl(text_embed, visual_fp)


def _heuristic_price_action(
    img: Image.Image,
    label: str,
    *,
    path: Path | None = None,
    sequence_index: int = 0,
) -> dict[str, Any]:
    impl = cast(Callable[..., dict[str, Any]], getattr(_impl, "_heuristic_price_action"))
    return impl(img, label, path=path, sequence_index=sequence_index)


def _passes_indicator_filter(text: str) -> bool:
    impl = cast(Callable[[str], bool], getattr(_impl, "_passes_indicator_filter"))
    return impl(text)


def _visual_fingerprint(img: Image.Image) -> NDArray[np.float32]:
    impl = cast(Callable[[Image.Image], NDArray[np.float32]], getattr(_impl, "_visual_fingerprint"))
    return impl(img)

__all__ = [
    "EMBED_DIM",
    "SHARED_DIM",
    "VISUAL_DIM",
    "MemoryBank",
    "MemoryEntry",
    "MemoryIngestor",
    "RecallResult",
    "_HNSWIndex",
    "_chart_state_to_text",
    "_dual_encode",
    "_heuristic_price_action",
    "_passes_indicator_filter",
    "_visual_fingerprint",
]
