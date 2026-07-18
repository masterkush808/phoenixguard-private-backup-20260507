"""Isolated real-model verification used by ``test_real_models``.

The SentenceTransformer/PyTorch native runtime is intentionally loaded in a
short-lived process.  This keeps its DLL state and large private allocation out
of the long-running pytest (and VS Code) process while still exercising the
real cached model weights and real inference path.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np


_RESULT_PREFIX = "PHOENIXGUARD_REAL_MODEL_RESULT="
_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
_REPO = Path(__file__).resolve().parents[3]


class _SentenceTransformerLike(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...


class _PreferenceStore:
    def insert_preference(self, row: dict[str, str], /) -> None:
        _ = row

    def fetch_recent(self, limit: int = 200, /) -> list[dict[str, str]]:
        _ = limit
        return []


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)

    def warning(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)


def _emit(payload: dict[str, Any]) -> None:
    print(f"{_RESULT_PREFIX}{json.dumps(payload, ensure_ascii=True, sort_keys=True)}", flush=True)


def _ensure_repo_paths() -> None:
    for candidate in (
        _REPO / "Backend" / "src",
        _REPO / "Backend",
        _REPO / "Backend" / "compat",
        _REPO / "Backend" / "launch",
        _REPO / "Frontend" / "dashboard",
        _REPO,
    ):
        candidate_text = str(candidate)
        if candidate.exists() and candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)


def _run_sentence_transformer_contract() -> dict[str, Any]:
    _ensure_repo_paths()

    # Apply caps inside the process as well as in the parent-provided
    # environment.  Inter-op must be set before inference starts.
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)

    raw_model = SentenceTransformer(
        _MODEL_ID,
        device="cpu",
        local_files_only=True,
        model_kwargs={
            "low_cpu_mem_usage": False,
            "attn_implementation": "eager",
        },
    )
    model = cast(_SentenceTransformerLike, raw_model)

    repeated = "SELL signal at resistance with upper wick rejection"
    texts = [
        "BUY entry after 4 red candles with wick rejection",
        "strong bullish breakout above resistance",
        "bearish reversal after weak close near low",
        repeated,
        repeated,
    ]
    encoded = np.asarray(
        model.encode(
            texts,
            batch_size=len(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ),
        dtype=np.float32,
    )
    if encoded.ndim != 2 or encoded.shape != (len(texts), 384):
        raise AssertionError(f"unexpected embedding shape: {encoded.shape!r}")

    first_norm = float(np.linalg.norm(encoded[0]))
    contrast_cosine = float(np.dot(encoded[1], encoded[2]))
    same_max_abs_diff = float(np.max(np.abs(encoded[3] - encoded[4])))

    # Exercise PersonalizationEngine with the exact same real model instance.
    # Reusing it is deliberate: constructing a second copy adds hundreds of MB
    # without increasing integration coverage.
    from phoenixguard.decision.personalization import PersonalizationEngine

    engine = PersonalizationEngine(_MODEL_ID, _PreferenceStore(), _NullLogger())
    engine.embedder = model
    setattr(engine, "_embedder_load_attempted", True)
    style_vector = engine.update_style_from_memory_bank(
        [
            {
                "chosen": "BUY after wick rejection",
                "rejected": "HOLD",
                "reason": "clear reversal",
            },
            {
                "chosen": "SELL after exhaustion",
                "rejected": "HOLD",
                "reason": "five consecutive green candles",
            },
        ]
    )
    prefix = engine.style_prefix_prompt()

    return {
        "ok": True,
        "model_class": f"{type(raw_model).__module__}.{type(raw_model).__qualname__}",
        "device": str(getattr(raw_model, "device", "cpu")),
        "embedding_dim": int(encoded.shape[1]),
        "all_finite": bool(np.all(np.isfinite(encoded))),
        "first_norm": first_norm,
        "contrast_cosine": contrast_cosine,
        "same_max_abs_diff": same_max_abs_diff,
        "personalization_used_real_model": bool(engine.embedder is model),
        "style_norm": float(np.linalg.norm(style_vector)),
        "style_prefix": prefix,
        "torch_threads": int(torch.get_num_threads()),
        "torch_interop_threads": int(torch.get_num_interop_threads()),
        "pid": int(os.getpid()),
    }


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "sentence-transformer-contract":
        _emit({"ok": False, "error": "unsupported worker command"})
        return 2
    try:
        _emit(_run_sentence_transformer_contract())
        return 0
    except Exception as exc:
        _emit(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
