from __future__ import annotations

import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.decision.personalization import PersonalizationEngine
from phoenixguard.memory import memory_ingest


class _PrefStore:
    def insert_preference(self, row: dict[str, str], /) -> None:
        return None

    def fetch_recent(self, limit: int = 200, /) -> list[dict[str, str]]:
        return []


class _Logger:
    def info(self, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, *args: object, **kwargs: object) -> None:
        return None


def test_personalization_embedder_defaults_to_cache_only(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeSentenceTransformer:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            captured["model_name"] = model_name
            captured["kwargs"] = dict(kwargs)

    fake_module = types.SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(
        "phoenixguard.decision.personalization.can_import_sentence_transformers_safely",
        lambda: True,
    )
    monkeypatch.delenv("PHOENIXGUARD_TEXT_EMBEDDER_ALLOW_REMOTE_BOOTSTRAP", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_TEXT_EMBEDDER_FORCE_DOWNLOAD", raising=False)

    engine = PersonalizationEngine(
        "sentence-transformers/all-MiniLM-L6-v2",
        _PrefStore(),
        _Logger(),
    )
    engine._ensure_embedder()

    assert captured["model_name"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert dict(captured["kwargs"])["local_files_only"] is True


def test_memory_ingest_embedder_defaults_to_cache_only(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeSentenceTransformer:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            captured["model_name"] = model_name
            captured["kwargs"] = dict(kwargs)

    fake_module = types.SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(memory_ingest, "can_import_sentence_transformers_safely", lambda: True)
    monkeypatch.delenv("PHOENIXGUARD_TEXT_EMBEDDER_ALLOW_REMOTE_BOOTSTRAP", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_TEXT_EMBEDDER_FORCE_DOWNLOAD", raising=False)

    memory_ingest.SentenceTransformer = None  # type: ignore[assignment]
    memory_ingest._EmbedderSingleton._instance = None
    memory_ingest._EmbedderSingleton._model = None

    memory_ingest._EmbedderSingleton.get()

    assert captured["model_name"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert dict(captured["kwargs"])["local_files_only"] is True

