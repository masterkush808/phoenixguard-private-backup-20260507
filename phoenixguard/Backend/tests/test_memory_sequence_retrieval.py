from __future__ import annotations

import json
import numpy as np
from numpy.typing import NDArray
from pathlib import Path
import sys
from typing import Any, cast

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import phoenixguard.memory.memory_ingest as memory_ingest_module
from phoenixguard.memory.memory_ingest import MemoryBank, MemoryEntry


class _DummyIndex:
    def __init__(self, sims: dict[str, float]) -> None:
        self._sims = sims

    def search(self, query_embed: NDArray[np.float32], top_k: int = 5) -> list[tuple[str, float]]:
        _ = query_embed
        ranked = sorted(self._sims.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


def _make_embed(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    vec: NDArray[np.float32] = rng.normal(size=(384,)).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    vec = (vec / max(norm, 1e-8)).astype(np.float32)
    return vec.tolist()


def test_memory_entry_from_dict_is_backward_compatible() -> None:
    raw: dict[str, Any] = {
        "entry_id": "a",
        "image_path": "a.png",
        "label": "BUY",
        "chart_state": {},
        "text_embed": _make_embed(1),
        "visual_fp": [0.0] * 128,
        "combined_embed": _make_embed(2),
    }
    entry = MemoryEntry.from_dict(raw)
    assert entry.episode_id == ""
    assert entry.local_phase == "with_trend_push"
    assert entry.intent_next == "continue"


def test_memory_entry_missing_label_defaults_to_hold() -> None:
    raw: dict[str, Any] = {
        "entry_id": "no-label",
        "image_path": "no-label.png",
        "chart_state": {},
        "text_embed": _make_embed(5),
        "visual_fp": [0.0] * 128,
        "combined_embed": _make_embed(6),
    }

    entry = MemoryEntry.from_dict(raw)

    assert entry.label == "HOLD"


def test_memory_entry_from_legacy_vlm_json_recovers_taxonomy() -> None:
    raw: dict[str, Any] = {
        "entry_id": "legacy-sell",
        "image_path": "legacy-sell.png",
        "label": "SELL",
        "vlm_json": {
            "direction": "SELL",
            "momentum_bias": "bearish",
            "entry_type": "continuation",
            "continuation_signal": "impulse_pause",
            "consolidation_type": "tight",
            "structure_setup": "impulse_chain",
        },
        "text_embed": _make_embed(3),
        "visual_fp": [0.0] * 128,
        "combined_embed": _make_embed(4),
    }

    entry = MemoryEntry.from_dict(raw)

    assert entry.chart_state["direction"] == "SELL"
    assert entry.macro_trend == "BEAR"
    assert entry.local_phase == "with_trend_pause"
    assert entry.phase_risk == "chop_risk"
    assert entry.intent_next == "continue"


def test_memory_entry_backfills_teaching_and_aggressive_sniper_context() -> None:
    raw: dict[str, Any] = {
        "entry_id": "buy-profit",
        "image_path": "700pips profit.png",
        "label": "BUY",
        "chart_state": {
            "direction": "BUY",
            "entry_type": "continuation",
            "continuation_signal": "impulse_pause",
            "momentum_bias": "bullish",
        },
        "text_embed": _make_embed(7),
        "visual_fp": [0.0] * 128,
        "combined_embed": _make_embed(8),
        "sequence_index": 2,
    }

    entry = MemoryEntry.from_dict(raw)

    teaching = cast(dict[str, Any], entry.chart_state["memory_teaching"])
    progression = cast(dict[str, Any], entry.chart_state["entry_progression"])
    sniper = cast(dict[str, Any], entry.chart_state["sniper_profile"])
    assert teaching["lesson_role"] == "win_resolution"
    assert "win_resolution" in teaching["tags"]
    assert float(teaching["teaching_weight"]) >= 0.90
    assert progression["progression_stage"] == "win_resolution"
    assert float(sniper["aggressive_entry_score"]) > 0.0


def test_sequence_context_and_transition_summary() -> None:
    entries = [
        MemoryEntry(
            entry_id="e1",
            image_path="1.png",
            label="SELL",
            chart_state={},
            text_embed=_make_embed(10),
            visual_fp=[0.0] * 128,
            combined_embed=_make_embed(11),
            episode_id="SELL:ep1",
            sequence_index=0,
            macro_trend="BEAR",
            local_phase="counter_trend_pullback",
            phase_risk="exhaustion_risk",
            intent_next="continue",
        ),
        MemoryEntry(
            entry_id="e2",
            image_path="2.png",
            label="SELL",
            chart_state={},
            text_embed=_make_embed(12),
            visual_fp=[0.0] * 128,
            combined_embed=_make_embed(13),
            episode_id="SELL:ep1",
            sequence_index=1,
            macro_trend="BEAR",
            local_phase="counter_trend_pullback",
            phase_risk="chop_risk",
            intent_next="pullback",
        ),
        MemoryEntry(
            entry_id="e3",
            image_path="3.png",
            label="BUY",
            chart_state={},
            text_embed=_make_embed(14),
            visual_fp=[0.0] * 128,
            combined_embed=_make_embed(15),
            episode_id="BUY:ep2",
            sequence_index=0,
            macro_trend="BULL",
            local_phase="with_trend_push",
            phase_risk="breakout_risk",
            intent_next="continue",
        ),
    ]

    idx = _DummyIndex({"e1": 0.89, "e2": 0.86, "e3": 0.84})

    bank = MemoryBank()
    bank.populate(index=cast(Any, idx), entries={e.entry_id: e for e in entries}, n_buy=1, n_sell=2)

    query = np.asarray(entries[0].combined_embed, dtype=np.float32)
    results = bank.search_sequence_context(
        query_embed=query,
        macro_trend="BEAR",
        local_phase="counter_trend_pullback",
        top_k=2,
    )

    assert results
    summary = bank.episode_summary(results)
    assert summary[0]["macro_trend"] == "BEAR"

    probs = bank.summarize_transition_probabilities(results)
    assert set(probs.keys()) == {"continue", "pullback", "reversal_attempt", "fakeout"}
    assert abs(sum(probs.values()) - 1.0) < 1e-6


def test_search_expands_centroid_shortlist_to_cluster_members() -> None:
    centroid_embed = _make_embed(20)
    exact_member_embed = _make_embed(21)
    other_embed = _make_embed(22)

    entries = [
        MemoryEntry(
            entry_id="centroid_buy",
            image_path="centroid.png",
            label="BUY",
            chart_state={"direction": "BUY"},
            text_embed=_make_embed(23),
            visual_fp=[0.0] * 128,
            combined_embed=centroid_embed,
            episode_id="BUY:ep-centroid",
            sequence_index=0,
            macro_trend="BULL",
            local_phase="with_trend_push",
            phase_risk="breakout_risk",
            intent_next="continue",
            archetype_id=7,
            is_archetype_centroid=True,
        ),
        MemoryEntry(
            entry_id="member_buy_exact",
            image_path="member.png",
            label="BUY",
            chart_state={"direction": "BUY"},
            text_embed=_make_embed(24),
            visual_fp=[0.0] * 128,
            combined_embed=exact_member_embed,
            episode_id="BUY:ep-member",
            sequence_index=1,
            macro_trend="BULL",
            local_phase="with_trend_push",
            phase_risk="breakout_risk",
            intent_next="continue",
            archetype_id=7,
            is_archetype_centroid=False,
        ),
        MemoryEntry(
            entry_id="other_sell",
            image_path="other.png",
            label="SELL",
            chart_state={"direction": "SELL"},
            text_embed=_make_embed(25),
            visual_fp=[0.0] * 128,
            combined_embed=other_embed,
            episode_id="SELL:ep-other",
            sequence_index=0,
            macro_trend="BEAR",
            local_phase="counter_trend_pullback",
            phase_risk="chop_risk",
            intent_next="pullback",
            archetype_id=1,
            is_archetype_centroid=True,
        ),
    ]

    idx = _DummyIndex({"centroid_buy": 0.82, "other_sell": 0.80})
    bank = MemoryBank()
    bank.populate(index=cast(Any, idx), entries={entry.entry_id: entry for entry in entries}, n_buy=2, n_sell=1)

    old_threshold = memory_ingest_module.FULL_ENTRY_SCAN_THRESHOLD
    memory_ingest_module.FULL_ENTRY_SCAN_THRESHOLD = 0
    try:
        query = np.asarray(exact_member_embed, dtype=np.float32)
        results = bank.search(query_embed=query, top_k=2)
    finally:
        memory_ingest_module.FULL_ENTRY_SCAN_THRESHOLD = old_threshold

    assert results
    assert results[0].entry_id == "member_buy_exact"
    assert results[0].is_archetype_centroid is False
    assert float(results[0].similarity) >= float(results[1].similarity)


def test_memory_bank_load_backfills_sequence_metadata(tmp_path: Path) -> None:
    bank_dir = tmp_path / "memory_bank"
    index_dir = bank_dir / "index"
    index_dir.mkdir(parents=True)

    metadata: list[dict[str, Any]] = [
        {
            "entry_id": "buy-a",
            "image_path": str(tmp_path / "Screenshot 2026-04-21 120000.png"),
            "label": "BUY",
            "chart_state": {
                "direction": "BUY",
                "entry_type": "continuation",
                "continuation_signal": "impulse_pause",
                "momentum_bias": "bullish",
            },
            "text_embed": _make_embed(31),
            "visual_fp": [0.0] * 128,
            "combined_embed": _make_embed(32),
            "episode_id": None,
            "sequence_index": None,
        },
        {
            "entry_id": "buy-b",
            "image_path": str(tmp_path / "Screenshot 2026-04-21 120500.png"),
            "label": "BUY",
            "chart_state": {
                "direction": "BUY",
                "entry_type": "continuation",
                "continuation_signal": "impulse_pause",
                "momentum_bias": "bullish",
            },
            "text_embed": _make_embed(33),
            "visual_fp": [0.0] * 128,
            "combined_embed": _make_embed(34),
        },
    ]
    (bank_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (index_dir / "id_map.json").write_text(json.dumps(["buy-a", "buy-b"]), encoding="utf-8")
    np.save(
        str(index_dir / "numpy_vecs.npy"),
        np.asarray([_make_embed(32), _make_embed(34)], dtype=np.float32),
    )

    bank = MemoryBank.load(bank_dir)

    assert bank.is_loaded is True
    loaded = {entry.entry_id: entry for entry in bank.entries}
    assert loaded["buy-a"].episode_id == loaded["buy-b"].episode_id
    assert loaded["buy-a"].sequence_index == 0
    assert loaded["buy-b"].sequence_index == 1

    persisted = cast(list[dict[str, Any]], json.loads((bank_dir / "metadata.json").read_text(encoding="utf-8")))
    persisted_by_id = {row["entry_id"]: row for row in persisted}
    assert persisted_by_id["buy-a"]["episode_id"] == persisted_by_id["buy-b"]["episode_id"]
    assert persisted_by_id["buy-a"]["sequence_index"] == 0
    assert persisted_by_id["buy-b"]["sequence_index"] == 1


def test_memory_bank_load_does_not_rescan_complete_entry_progression(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bank_dir = tmp_path / "memory_bank"
    bank_dir.mkdir()
    image_path = tmp_path / "complete-buy.png"
    image_path.write_bytes(b"the complete state makes this file intentionally unreadable")
    regression = {
        "slope": -0.04,
        "direction": "BUY",
        "pressure_direction": "BUY",
        "confidence": 0.84,
        "alignment_to_label": 1.0,
        "recent_activity_columns": 18,
    }
    progression = {
        "progression_stage": "progression",
        "entry_x_norm": 0.78,
        "entry_y_norm": 0.52,
        "sniper_y_norm": 0.55,
        "trigger_y_norm": 0.49,
        "target_y_norm": 0.36,
        "invalidation_y_norm": 0.64,
        "entry_window_norm": [0.68, 0.48, 0.90, 0.56],
        "sniper_window_norm": [0.68, 0.52, 0.90, 0.58],
        "trigger_window_norm": [0.68, 0.46, 0.90, 0.52],
        "target_window_norm": [0.68, 0.32, 0.90, 0.40],
        "compression_score": 0.72,
        "pullback_depth": 0.28,
        "rejection_score": 0.81,
        "follow_through_score": 0.77,
        "aggressive_sniper_score": 0.74,
        "candle_regression": regression,
        "candle_regression_slope": -0.04,
        "candle_regression_direction": "BUY",
        "regression_confidence": 0.84,
        "favorable_pressure": 0.76,
        "opposing_pressure": 0.18,
        "recent_activity_columns": 18,
    }
    complete_chart_state: dict[str, Any] = {
        "direction": "BUY",
        "entry_type": "continuation",
        "continuation_signal": "impulse_pause",
        "momentum_bias": "bullish",
        "memory_teaching": {
            "lesson_role": "progression",
            "tags": ["progression"],
            "source_name": image_path.name,
            "label": "BUY",
            "sequence_index": 1,
            "actual_entry_score": 0.0,
            "win_evidence_score": 0.0,
            "progression_score": 0.55,
            "teaching_weight": 0.55,
        },
        "entry_progression": progression,
        "memory_candle_regression": regression,
        "sniper_profile": {
            "style": "aggressive_sniper",
            "lesson_role": "progression",
            "aggressive_entry_score": 0.74,
            "watch_window_norm": progression["sniper_window_norm"],
            "entry_window_norm": progression["entry_window_norm"],
            "trigger_window_norm": progression["trigger_window_norm"],
            "target_window_norm": progression["target_window_norm"],
            "invalidation_y_norm": 0.64,
            "instruction": "Persisted memory entry instruction.",
        },
        "aggressive_entry_score": 0.74,
    }
    metadata = [
        {
            "entry_id": "complete-buy",
            "image_path": str(image_path),
            "label": "BUY",
            "chart_state": complete_chart_state,
            "text_embed": _make_embed(35),
            "visual_fp": [0.0] * 128,
            "combined_embed": _make_embed(36),
            "episode_id": "BUY:complete",
            "sequence_index": 1,
        }
    ]
    (bank_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    def fail_if_opened(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("complete persisted progression must not reopen its source image")

    monkeypatch.setattr(memory_ingest_module.Image, "open", fail_if_opened)

    bank = MemoryBank.load(bank_dir)

    assert bank.is_loaded is True
    assert len(bank.entries) == 1
    assert bank.entries[0].chart_state["entry_progression"] == complete_chart_state["entry_progression"]


def test_memory_bank_load_rescans_image_for_incomplete_entry_progression(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bank_dir = tmp_path / "memory_bank"
    bank_dir.mkdir()
    image_path = tmp_path / "legacy-sell.png"
    memory_ingest_module.Image.new("RGB", (32, 20), color=(180, 20, 40)).save(image_path)
    metadata = [
        {
            "entry_id": "legacy-sell",
            "image_path": str(image_path),
            "label": "SELL",
            "chart_state": {
                "direction": "SELL",
                "entry_type": "continuation",
                "continuation_signal": "impulse_pause",
                "momentum_bias": "bearish",
                "entry_progression": {"progression_stage": "legacy_partial"},
            },
            "text_embed": _make_embed(37),
            "visual_fp": [0.0] * 128,
            "combined_embed": _make_embed(38),
            "episode_id": "SELL:legacy",
            "sequence_index": 1,
        }
    ]
    (bank_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    original_open = memory_ingest_module.Image.open
    opened_paths: list[Path] = []

    def track_open(path: str | Path, *args: Any, **kwargs: Any) -> Any:
        opened_paths.append(Path(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(memory_ingest_module.Image, "open", track_open)

    bank = MemoryBank.load(bank_dir)

    assert bank.is_loaded is True
    assert opened_paths == [image_path]
    chart_state = bank.entries[0].chart_state
    progression = cast(dict[str, Any], chart_state["entry_progression"])
    assert progression["progression_stage"] == "legacy_partial"
    assert "entry_x_norm" in progression
    assert "candle_regression" in progression
    assert chart_state["memory_teaching"]
    assert chart_state["sniper_profile"]


def test_memory_bank_load_survives_incompatible_hnsw_index(tmp_path: Path) -> None:
    bank_dir = tmp_path / "memory_bank"
    index_dir = bank_dir / "index"
    index_dir.mkdir(parents=True)
    metadata: list[dict[str, Any]] = [
        {
            "entry_id": "sell-a",
            "image_path": str(tmp_path / "sell-a.png"),
            "label": "SELL",
            "chart_state": {"direction": "SELL", "entry_type": "continuation"},
            "text_embed": _make_embed(41),
            "visual_fp": [0.0] * 128,
            "combined_embed": _make_embed(42),
        },
        {
            "entry_id": "sell-b",
            "image_path": str(tmp_path / "sell-b.png"),
            "label": "SELL",
            "chart_state": {"direction": "SELL", "entry_type": "continuation"},
            "text_embed": _make_embed(43),
            "visual_fp": [0.0] * 128,
            "combined_embed": _make_embed(44),
        },
    ]
    (bank_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (index_dir / "id_map.json").write_text(json.dumps(["sell-a", "sell-b"]), encoding="utf-8")
    (index_dir / "hnsw.bin").write_bytes(b"not a compatible hnsw index")
    np.save(
        str(index_dir / "numpy_vecs.npy"),
        np.asarray([_make_embed(42), _make_embed(44)], dtype=np.float32),
    )

    bank = MemoryBank.load(bank_dir)
    results = bank.search(np.asarray(_make_embed(42), dtype=np.float32), top_k=1)

    assert bank.is_loaded is True
    assert len(bank.entries) == 2
    assert results
    assert results[0].entry_id == "sell-a"
