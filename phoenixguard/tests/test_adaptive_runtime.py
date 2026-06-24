from __future__ import annotations
from typing import Any

import tempfile
from pathlib import Path
import sys

import numpy as np
import numpy.typing as npt
from PIL import Image


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.runtime.adaptive_runtime import (
    ContinualLearningManager,
    OpenSetDetector,
    summarize_grounded_structure,
    build_artifact_summary,
    build_grounded_chart,
)
from phoenixguard.memory.memory_ingest import MemoryBank, MemoryEntry


class _DummyIndex:
    def __init__(self, sims: dict[str, float]) -> None:
        self._sims = sims

    def search(self, query: npt.NDArray[np.float32], top_k: int = 5) -> list[tuple[str, float]]:
        _ = query
        ranked = sorted(self._sims.items(), key=lambda item: item[1], reverse=True)
        return ranked[:top_k]

    def save(self, path: Path) -> None:
        _ = path

    def load(self, path: Path, n_entries: int) -> None:
        _ = path, n_entries


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, *args: object, **kwargs: object) -> None:
        return None


def _make_embed(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    vec = rng.normal(size=(384,)).astype(np.float32)
    vec /= max(float(np.linalg.norm(vec)), 1e-8)
    return vec.tolist()


def test_late_interaction_context_reranks_memory_hits() -> None:
    base_embed = _make_embed(10)
    bank = MemoryBank()
    entries = {
        "match": MemoryEntry(
            entry_id="match",
            image_path="match.png",
            label="BUY",
            chart_state={"direction": "BUY"},
            text_embed=_make_embed(11),
            visual_fp=[0.0] * 128,
            combined_embed=base_embed,
            late_interaction_tokens=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            trajectory_signature=[1.0, 0.0, 0.0],
            style_signature={"contrast": 0.8, "dark_theme": 1.0},
        ),
        "mismatch": MemoryEntry(
            entry_id="mismatch",
            image_path="mismatch.png",
            label="BUY",
            chart_state={"direction": "BUY"},
            text_embed=_make_embed(12),
            visual_fp=[0.0] * 128,
            combined_embed=base_embed,
            late_interaction_tokens=[[0.0, 0.0, 1.0]],
            trajectory_signature=[0.0, 1.0, 0.0],
            style_signature={"contrast": 0.1, "dark_theme": 0.0},
        ),
    }
    bank.populate(index=_DummyIndex({"mismatch": 0.91, "match": 0.90}), entries=entries, n_buy=2, n_sell=0)

    results = bank.search(
        np.asarray(base_embed, dtype=np.float32),
        top_k=2,
        query_context={
            "late_interaction_tokens": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "trajectory_signature": [1.0, 0.0, 0.0],
            "style_signature": {"contrast": 0.8, "dark_theme": 1.0},
        },
    )

    assert results
    assert results[0].entry_id == "match"


def test_open_set_detector_flags_artifact_heavy_capture() -> None:
    detector = OpenSetDetector(_NullLogger())
    img = Image.new("RGB", (128, 128), color=(0, 0, 0))
    arr = np.asarray(img, dtype=np.uint8).copy()
    for col in range(0, arr.shape[1], 8):
        arr[:, col:col + 4, :] = 255
    striped = Image.fromarray(arr)
    artifact = build_artifact_summary(striped, chart_geometry={"geometry_confidence": 0.1})
    summary = detector.assess(
        style_signature={"contrast": 1.0, "dark_theme": 1.0, "aspect_ratio": 1.0},
        artifact_summary=artifact,
        chart_geometry={"geometry_confidence": 0.1},
        sequence_state={"box_sequence_agreement": 0.1, "color_flip_rate": 0.8},
        local_ensemble={"ensemble": {"disagreement": 0.5, "entropy": 0.9}},
        memory_reference={"mean": {"contrast": 0.4, "dark_theme": 0.0}, "std": {"contrast": 0.1, "dark_theme": 0.1}},
        memory_summary={"ambiguity": 0.4},
    )

    assert float(summary["artifact_score"]) > 0.5
    assert bool(summary["force_hold"]) is True
    assert "artifact_heavy" in summary["flags"]


def test_continual_learning_feedback_updates_adapter_bank() -> None:
    with tempfile.TemporaryDirectory() as td:
        manager = ContinualLearningManager(Path(td), _NullLogger(), replay_buffer_size=10)
        manager.record_inference_context(
            image_hash="abc",
            context_key="dark|standard|mid|M5|reversal",
            context_descriptor="dark standard M5 reversal",
            local_ensemble={"ensemble": {"champion_model": "clip", "confirmer_model": "dinov2"}},
            predicted_action="BUY",
            confidence=0.88,
            style_signature={"dark_theme": 1.0},
            ood_summary={"ood_score": 0.12},
            source_path="C:/charts/example.png",
            snapshot_image=Image.new("RGB", (24, 24), color=(16, 16, 16)),
        )
        feedback_path = Path(td) / "feedback_result.png"
        Image.new("RGB", (28, 28), color=(220, 180, 64)).save(feedback_path)

        replay = manager.record_feedback(
            "abc",
            "BUY",
            "clean continuation",
            feedback_image_path=str(feedback_path),
            feedback_image_sha256="feedback_sha256",
            feedback_image_meta={"width": 28, "height": 28},
        )
        profile = manager.adapter_profile_for_context("dark|standard|mid|M5|reversal")

        assert replay["success"] is True
        assert int(profile["count"]) == 1
        assert float(profile["success_rate"]) > 0.5
        assert float(profile["model_weight_biases"]["clip"]) > 0.0
        assert str(replay["source_path"]).endswith("example.png")
        assert str(replay["snapshot_path"]) == str(feedback_path)
        assert str(replay["inference_snapshot_path"]).endswith(".png")
        assert str(replay["feedback_image_path"]) == str(feedback_path)
        assert not list(Path(td).rglob("*.tmp"))


def test_derive_context_key_includes_pair_identity_when_available() -> None:
    with tempfile.TemporaryDirectory() as td:
        manager = ContinualLearningManager(Path(td), _NullLogger(), replay_buffer_size=10)

        legacy_key = manager.derive_context_key(
            {"dark_theme": 1.0, "aspect_ratio": 1.7, "candle_density": 0.5},
            chart_state={"timeframe": "m15", "structure_setup": "reversal"},
        )
        paired_key = manager.derive_context_key(
            {"dark_theme": 1.0, "aspect_ratio": 1.7, "candle_density": 0.5},
            chart_state={"timeframe": "m15", "structure_setup": "reversal", "symbol": "EURUSD"},
        )

        assert legacy_key == "dark|wide|mid|M15|reversal"
        assert paired_key == "dark|wide|mid|M15|reversal|eurusd"


def test_grounded_chart_merges_optional_backend_regions() -> None:
    class _BackendResult:
        caption = "candles near support"
        detections: list[dict[str, Any]] = [
            {"label": "support zone", "score": 0.82, "bbox": [1.0, 2.0, 20.0, 25.0], "source": "grounding_dino"},
            {"label": "broker ui", "score": 0.91, "bbox": [0.0, 0.0, 10.0, 10.0], "source": "grounding_dino"},
        ]
        masks: list[dict[str, float]] = []
        used_backends = ["grounding_dino"]
        errors: dict[str, str] = {}
        confidence = 0.86

    class _BackendParser:
        def parse(self, image: Image.Image) -> _BackendResult:
            _ = image
            return _BackendResult()

    grounded = build_grounded_chart(
        Image.new("RGB", (128, 72), color=(0, 0, 0)),
        detections=[],
        chart_geometry={"geometry_confidence": 0.6},
        sequence_state={"all_visible_candles": [], "box_history": [], "spacing_consistency": 0.5},
        backend_parser=_BackendParser(),
    )

    assert bool(grounded["backend"]["available"]) is True
    assert "grounding_dino" in grounded["backend"]["used_backends"]
    assert any(str(zone.get("pattern", "")).startswith("support") for zone in grounded["zones"])
    assert float(grounded["artifact_summary"]["artifact_score"]) >= float(grounded["artifact_summary"]["ui_artifact_score"])


def test_metric_profile_context_reranks_memory_hits() -> None:
    base_embed = _make_embed(30)
    bank = MemoryBank()
    entries = {
        "match": MemoryEntry(
            entry_id="match",
            image_path="match.png",
            label="BUY",
            chart_state={"direction": "BUY"},
            text_embed=_make_embed(31),
            visual_fp=[0.0] * 128,
            combined_embed=base_embed,
            metric_profile={
                "direction_buy": 1.0,
                "sequence_buy_pressure": 0.84,
                "sequence_sell_pressure": 0.16,
                "support_strength": 0.68,
                "resistance_strength": 0.10,
                "structure_buy_pressure": 0.72,
                "structure_sell_pressure": 0.14,
            },
        ),
        "mismatch": MemoryEntry(
            entry_id="mismatch",
            image_path="mismatch.png",
            label="SELL",
            chart_state={"direction": "SELL"},
            text_embed=_make_embed(32),
            visual_fp=[0.0] * 128,
            combined_embed=base_embed,
            metric_profile={
                "direction_sell": 1.0,
                "sequence_buy_pressure": 0.20,
                "sequence_sell_pressure": 0.80,
                "support_strength": 0.08,
                "resistance_strength": 0.67,
                "structure_buy_pressure": 0.18,
                "structure_sell_pressure": 0.74,
            },
        ),
    }
    bank.populate(index=_DummyIndex({"mismatch": 0.91, "match": 0.90}), entries=entries, n_buy=1, n_sell=1)

    results = bank.search(
        np.asarray(base_embed, dtype=np.float32),
        top_k=2,
        query_context={
            "metric_profile": {
                "direction_buy": 1.0,
                "sequence_buy_pressure": 0.88,
                "sequence_sell_pressure": 0.12,
                "support_strength": 0.70,
                "resistance_strength": 0.12,
                "structure_buy_pressure": 0.76,
                "structure_sell_pressure": 0.10,
            }
        },
    )

    assert results
    assert results[0].entry_id == "match"


def test_grounded_chart_structure_summary_tracks_directional_bias() -> None:
    class _BackendResult:
        caption = "breakout above support"
        detections: list[dict[str, Any]] = [
            {"label": "support zone", "score": 0.88, "bbox": [2.0, 6.0, 18.0, 26.0], "source": "grounding_dino"},
            {"label": "breakout box", "score": 0.84, "bbox": [22.0, 6.0, 44.0, 28.0], "source": "grounding_dino"},
        ]
        masks: list[dict[str, float]] = []
        used_backends = ["grounding_dino"]
        errors: dict[str, str] = {}
        confidence = 0.83

    class _BackendParser:
        def parse(self, image: Image.Image) -> _BackendResult:
            _ = image
            return _BackendResult()

    grounded = build_grounded_chart(
        Image.new("RGB", (96, 64), color=(0, 0, 0)),
        detections=[{"pattern": "breakout", "confidence": 0.81, "bbox": [18.0, 8.0, 46.0, 30.0]}],
        chart_geometry={"geometry_confidence": 0.72},
        sequence_state={
            "all_visible_candles": [],
            "box_history": [
                {"box_type": "impulse", "direction": "BUY", "confidence": 0.78, "bbox": [20.0, 8.0, 48.0, 30.0]},
            ],
            "current_box": {"box_type": "impulse", "direction": "BUY", "confidence": 0.78, "consolidation_score": 0.18},
            "next_box_hypotheses": [{"box_type": "impulse", "direction": "BUY", "confidence": 0.74}],
            "spacing_consistency": 0.63,
            "box_sequence_agreement": 0.71,
            "path_clarity": 0.76,
            "continuation_probability": 0.62,
            "reversal_probability": 0.15,
            "fakeout_probability": 0.11,
        },
        backend_parser=_BackendParser(),
    )

    structure = grounded["structure_summary"]
    assert float(structure["support_strength"]) > 0.0
    assert float(structure["breakout_strength"]) > 0.0
    assert str(structure["structure_bias_direction"]) == "BUY"


def test_grounded_structure_respects_bearish_breakout_direction() -> None:
    structure = summarize_grounded_structure(
        objects=[],
        zones=[
            {
                "kind": "sequence_box",
                "box_type": "impulse",
                "direction": "SELL",
                "confidence": 0.86,
            }
        ],
        current_box={"box_type": "impulse", "direction": "SELL", "confidence": 0.82, "consolidation_score": 0.14},
        next_boxes=[{"box_type": "impulse", "direction": "SELL", "confidence": 0.79}],
        sequence_state={
            "spacing_consistency": 0.72,
            "box_sequence_agreement": 0.78,
            "path_clarity": 0.81,
            "continuation_probability": 0.69,
            "reversal_probability": 0.18,
            "fakeout_probability": 0.11,
        },
        chart_geometry={"geometry_confidence": 0.74},
    )

    assert float(structure["breakout_strength"]) > 0.0
    assert float(structure["sell_pressure"]) > float(structure["buy_pressure"])
    assert str(structure["structure_bias_direction"]) == "SELL"
