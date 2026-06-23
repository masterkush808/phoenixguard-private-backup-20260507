import json
from pathlib import Path
from typing import Mapping, cast

from phoenixguard.vision import market_registry


def _overlay(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def write_jsonl(session_id: str, entries: list[dict[str, object]]) -> None:
    session_file = market_registry.REGISTRY_DIR / f"{session_id}.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    with session_file.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def test_load_market_objects_normalizes_bbox_and_truth(tmp_path):
    session_id = "test-normalize-session"
    entries = []
    # legacy overlay using anchors
    entries.append({
        "timestamp": "2026-01-01T00:00:00Z",
        "overlay": {"id": "a1", "anchors": [[10, 20], [110, 220]], "confidence": 0.82}
    })
    # legacy overlay using box
    entries.append({
        "timestamp": "2026-01-01T00:00:01Z",
        "overlay": {"id": "b2", "box": [5, 6, 55, 66], "confidence": 0.42}
    })
    # overlay with no spatial info
    entries.append({
        "timestamp": "2026-01-01T00:00:02Z",
        "overlay": {"id": "c3", "confidence": 0.95}
    })
    write_jsonl(session_id, entries)

    loaded = market_registry.load_market_objects(session_id)
    assert isinstance(loaded, list) and len(loaded) >= 3
    # find overlays by id
    overlays = {str(_overlay(e.get("overlay")).get("id") or ""): _overlay(e.get("overlay")) for e in loaded}
    assert "a1" in overlays and overlays["a1"].get("bbox") is not None
    assert "b2" in overlays and overlays["b2"].get("bbox") == [5.0, 6.0, 55.0, 66.0]
    assert "c3" in overlays and isinstance(overlays["c3"].get("truth_score"), float)
