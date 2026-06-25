from typing import Any
from pathlib import Path
import time

import pytest

import phoenixguard.vision.market_registry as market_registry
from phoenixguard.vision.market_registry import merge_market_objects, load_market_objects, query_active_objects, promote_lifecycle


def test_merge_and_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_registry, "REGISTRY_DIR", tmp_path / "market_registry")
    market_registry.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    session = f"test-session-lifecycle-2-{tmp_path.name}"
    # create two overlapping boxes (IoU > 0.5)
    o1: dict[str, Any] = {"id": "o1", "bbox": [0, 0, 10, 10], "confidence": 0.6}
    o2: dict[str, Any] = {"id": "o2", "bbox": [1, 1, 9, 9], "confidence": 0.8}

    appended = merge_market_objects(session, [o1])
    assert appended == 1
    appended2 = merge_market_objects(session, [o2])
    assert appended2 == 1

    entries = load_market_objects(session)
    assert len(entries) >= 2

    # query active with low threshold should include at least one (o2 is high confidence)
    active = query_active_objects(session, min_truth_score=0.0)
    assert any(e.get("overlay_id") in {"o1", "o2"} for e in active)

    # simulate aging by sleeping and calling promote_lifecycle with small TTL
    time.sleep(1)
    promote_lifecycle(session, stale_seconds=0)
    entries_after = load_market_objects(session)
    assert any(e.get("lifecycle_state") == "STALE" for e in entries_after)


def test_query_active_objects_filters_entries_past_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_registry, "REGISTRY_DIR", tmp_path / "market_registry")
    market_registry.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    session = f"test-session-lifecycle-ttl-{tmp_path.name}"
    merge_market_objects(session, [{"id": "ttl-old", "bbox": [0, 0, 10, 10], "confidence": 0.9}])
    time.sleep(0.01)

    active = query_active_objects(session, min_truth_score=0.0, stale_seconds=0)

    assert active == []
