from __future__ import annotations
import pytest

import sys
from pathlib import Path
from typing import Any

from PIL import Image

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.vision import cv_module
from phoenixguard.vision import grounded_backends
from phoenixguard.vision.cv_module import CVPatternDetector


class _Logger:
    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        return None

    def exception(self, msg: str, *args: object, **kwargs: object) -> None:
        return None

    def error(self, msg: str, *args: object, **kwargs: object) -> None:
        return None


def test_hf_bootstrap_stays_cache_only_until_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_download(*_args: object, **kwargs: object) -> None:
        calls.append(dict(kwargs))
        raise FileNotFoundError("cache miss")

    def _load_yolo_model(path: str) -> str:
        return path

    def _can_import_torchvision_safely() -> bool:
        return True

    def _load_or_train_memory_classifier(_self: CVPatternDetector) -> None:
        return None

    monkeypatch.delenv("PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_CV_FORCE_DOWNLOAD", raising=False)
    monkeypatch.setattr(cv_module, "YOLOModel", _load_yolo_model)
    monkeypatch.setattr(cv_module, "hf_hub_download", _fake_download)
    monkeypatch.setattr(cv_module, "can_import_torchvision_safely", _can_import_torchvision_safely)
    monkeypatch.setattr(CVPatternDetector, "_load_or_train_memory_classifier", _load_or_train_memory_classifier)

    detector = CVPatternDetector(
        primary_model="hf://demo/cv-model",
        fallback_model="hf://demo/cv-model",
        logger=_Logger(),
    )

    assert detector.model is None
    assert detector.strict_model_only is False
    assert len(calls) == 1
    assert calls[0]["local_files_only"] is True


def test_detect_uses_heuristic_fallback_when_raw_backend_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def _try_load_hf_yolo_weights(_self: CVPatternDetector, _model_ref: str) -> bool:
        return False

    def _raw_detect(_image_rgb: object) -> list[dict[str, Any]]:
        return []

    def _heuristic_candle_detect(_image_rgb: object) -> list[dict[str, Any]]:
        return [
            {"pattern": "reversal", "confidence": 0.78, "bbox": [1.0, 2.0, 3.0, 4.0], "source": "heuristic"}
        ]

    monkeypatch.setattr(CVPatternDetector, "_try_load_hf_yolo_weights", _try_load_hf_yolo_weights)
    detector = CVPatternDetector(
        primary_model="hf://demo/cv-model",
        fallback_model="hf://demo/cv-model",
        logger=_Logger(),
    )

    monkeypatch.setattr(detector, "_raw_detect", _raw_detect)
    monkeypatch.setattr(detector, "_heuristic_candle_detect", _heuristic_candle_detect)

    result = detector.detect(Image.new("RGB", (128, 128), color=(15, 15, 15)))

    assert len(result) == 1
    assert result[0]["pattern"] == "reversal"
    assert result[0]["pattern_type"] == "reversal"


def test_detect_returns_heuristic_structure_without_loaded_yolo(monkeypatch: pytest.MonkeyPatch) -> None:
    def _try_load_hf_yolo_weights(_self: CVPatternDetector, _model_ref: str) -> bool:
        return False

    def _raw_detect(_image_rgb: object) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(CVPatternDetector, "_try_load_hf_yolo_weights", _try_load_hf_yolo_weights)
    detector = CVPatternDetector(
        primary_model="hf://demo/cv-model",
        fallback_model="hf://demo/cv-model",
        logger=_Logger(),
    )

    monkeypatch.setattr(detector, "_raw_detect", _raw_detect)
    heuristic_rows: list[dict[str, Any]] = [
        {"pattern": "continuation", "confidence": 0.81, "bbox": [10.0, 10.0, 20.0, 40.0], "source": "heuristic"},
        {"pattern": "consolidation", "confidence": 0.62, "bbox": [5.0, 5.0, 25.0, 45.0], "source": "heuristic"},
    ]

    def _heuristic_candle_detect(_image_rgb: object) -> list[dict[str, Any]]:
        return list(heuristic_rows)

    monkeypatch.setattr(detector, "_heuristic_candle_detect", _heuristic_candle_detect)

    result = detector.detect(Image.new("RGB", (192, 192), color=(10, 10, 10)))
    names = [str(row["pattern"]) for row in result]

    assert "continuation" in names
    assert "consolidation" in names


def test_optional_grounded_parser_uses_cache_only_bootstrap_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class _FakeLoaded:
        def to(self, _device: object) -> "_FakeLoaded":
            return self

        def eval(self) -> "_FakeLoaded":
            return self

    class _FakeLoader:
        def __init__(self, label: str) -> None:
            self.label = label

        def from_pretrained(self, model_name: str, **kwargs: object) -> _FakeLoaded:
            calls.append({"loader": self.label, "model_name": model_name, **kwargs})
            return _FakeLoaded()

    class _FakeTransformers:
        AutoProcessor = _FakeLoader("AutoProcessor")
        AutoModelForCausalLM = _FakeLoader("AutoModelForCausalLM")
        AutoModelForZeroShotObjectDetection = _FakeLoader("AutoModelForZeroShotObjectDetection")
        SamProcessor = _FakeLoader("SamProcessor")
        SamModel = _FakeLoader("SamModel")

    def _load_transformers(_self: object) -> type[_FakeTransformers]:
        return _FakeTransformers

    monkeypatch.delenv("PHOENIXGUARD_GROUNDED_ALLOW_REMOTE_BOOTSTRAP", raising=False)
    monkeypatch.setattr(
        grounded_backends.OptionalGroundedParser,
        "_load_transformers",
        _load_transformers,
    )

    parser = grounded_backends.OptionalGroundedParser(_Logger())
    _ = parser.parse(Image.new("RGB", (8, 8), color=(0, 0, 0)))

    assert calls
    assert all(call["local_files_only"] is True for call in calls)
