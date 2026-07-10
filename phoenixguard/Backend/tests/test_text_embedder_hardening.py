from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Mapping, cast

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.decision.personalization import PersonalizationEngine  # noqa: E402
from phoenixguard.core import utils as core_utils  # noqa: E402
from phoenixguard.memory import memory_ingest  # noqa: E402


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


def test_sentence_transformer_probe_uses_configurable_cold_start_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _probe(import_stmt: str, timeout_sec: int = 20) -> bool:
        captured["import_stmt"] = import_stmt
        captured["timeout_sec"] = timeout_sec
        return True

    monkeypatch.setenv("PHOENIXGUARD_SENTENCE_TRANSFORMERS_IMPORT_TIMEOUT_SEC", "75")
    monkeypatch.setattr(core_utils, "can_import_module_safely", _probe)

    assert core_utils.can_import_sentence_transformers_safely() is True
    assert captured["import_stmt"] == "from sentence_transformers import SentenceTransformer"
    assert captured["timeout_sec"] == 75


def test_torchvision_probe_uses_configurable_cold_start_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _probe(import_stmt: str, timeout_sec: int = 20) -> bool:
        captured["import_stmt"] = import_stmt
        captured["timeout_sec"] = timeout_sec
        return True

    monkeypatch.setenv("PHOENIXGUARD_TORCHVISION_IMPORT_TIMEOUT_SEC", "70")
    monkeypatch.setattr(core_utils, "can_import_module_safely", _probe)

    assert core_utils.can_import_torchvision_safely() is True
    assert captured["import_stmt"] == "import torchvision"
    assert captured["timeout_sec"] == 70


def _captured_kwargs(captured: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], captured["kwargs"])


def test_personalization_embedder_defaults_to_cache_only(monkeypatch: pytest.MonkeyPatch) -> None:
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
    engine.ensure_embedder()

    assert captured["model_name"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert _captured_kwargs(captured)["local_files_only"] is True


def test_memory_ingest_embedder_defaults_to_cache_only(monkeypatch: pytest.MonkeyPatch) -> None:
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
    memory_ingest.reset_embedder_singleton_for_test()

    memory_ingest.EmbedderSingleton.get()

    assert captured["model_name"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert _captured_kwargs(captured)["local_files_only"] is True
