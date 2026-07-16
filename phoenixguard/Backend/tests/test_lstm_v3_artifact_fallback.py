from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from phoenixguard.decision import lstm_candle_sequence_contributor_v3 as contributor


_select_runtime_artifact_bundle = cast(
    Callable[
        [Path, Path, Path],
        tuple[dict[str, Any], Path, Path, Path, dict[str, Any]],
    ],
    getattr(contributor, "_select_runtime_artifact_bundle"),
)


def test_default_runtime_request_uses_direct_low_confidence_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, Path, Path]] = []

    def fake_load(model_path: Path, config_path: Path, metrics_path: Path) -> dict[str, object]:
        calls.append((model_path, config_path, metrics_path))
        if model_path == contributor.DIRECT_DIAGNOSTIC_MODEL_PATH:
            return {
                "config": {
                    "architecture": contributor.DIRECT_RAW_CV_ARCHITECTURE,
                    "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
                },
                "model_loaded": True,
                "legacy_restored": False,
                "ready": False,
            }
        return {
            "config": {
                "architecture": contributor.LEGACY_MULTISCALE_ARCHITECTURE,
            },
            "model_loaded": True,
            "legacy_restored": True,
            "ready": False,
        }

    monkeypatch.setattr(contributor, "_load_artifact_bundle", fake_load)

    bundle, model_path, config_path, metrics_path, selection = (
        _select_runtime_artifact_bundle(
            contributor.DEFAULT_MODEL_PATH,
            contributor.DEFAULT_CONFIG_PATH,
            contributor.DEFAULT_METRICS_PATH,
        )
    )

    assert bundle["model_loaded"] is True
    assert bundle["ready"] is False
    assert model_path == contributor.DIRECT_DIAGNOSTIC_MODEL_PATH
    assert config_path == contributor.DIRECT_DIAGNOSTIC_CONFIG_PATH
    assert metrics_path == contributor.DIRECT_DIAGNOSTIC_METRICS_PATH
    assert selection["source"] == "DIRECT_V3_LOW_CONFIDENCE_FALLBACK"
    assert selection["fallback_used"] is True
    assert selection["selected_path_target_semantics"] == "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR"
    assert selection["selected_production_authorized"] is False
    assert selection["canonical_audit"]["architecture"] == contributor.LEGACY_MULTISCALE_ARCHITECTURE
    assert calls == [
        (
            contributor.DEFAULT_MODEL_PATH,
            contributor.DEFAULT_CONFIG_PATH,
            contributor.DEFAULT_METRICS_PATH,
        ),
        (
            contributor.DIRECT_DIAGNOSTIC_MODEL_PATH,
            contributor.DIRECT_DIAGNOSTIC_CONFIG_PATH,
            contributor.DIRECT_DIAGNOSTIC_METRICS_PATH,
        ),
    ]


def test_explicit_artifact_request_never_silently_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit_paths = (
        tmp_path / "explicit.pt",
        tmp_path / "explicit_config.json",
        tmp_path / "explicit_metrics.json",
    )
    explicit_bundle: dict[str, object] = {
        "config": {"architecture": contributor.LEGACY_MULTISCALE_ARCHITECTURE},
        "model_loaded": True,
        "legacy_restored": True,
        "ready": False,
    }
    calls: list[tuple[Path, Path, Path]] = []

    def fake_load(model_path: Path, config_path: Path, metrics_path: Path) -> dict[str, object]:
        calls.append((model_path, config_path, metrics_path))
        return explicit_bundle

    monkeypatch.setattr(contributor, "_load_artifact_bundle", fake_load)

    bundle, model_path, config_path, metrics_path, selection = (
        _select_runtime_artifact_bundle(*explicit_paths)
    )

    assert bundle is explicit_bundle
    assert (model_path, config_path, metrics_path) == explicit_paths
    assert selection["source"] == "CANONICAL_V3"
    assert selection["fallback_used"] is False
    assert calls == [explicit_paths]
