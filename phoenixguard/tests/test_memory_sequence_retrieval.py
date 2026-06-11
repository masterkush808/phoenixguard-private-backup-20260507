from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from pathlib import Path
import sys
from typing import Any, cast

_REPO = Path(__file__).resolve().parent.parent
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
