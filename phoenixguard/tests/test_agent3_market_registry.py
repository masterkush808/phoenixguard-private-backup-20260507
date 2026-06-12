from phoenixguard.vision.market_registry import persist_market_objects, load_market_objects
from pathlib import Path

def test_market_registry_persist_and_load(tmp_path):
    session_id = "test-session-xyz"
    # create demo objects
    objs = [
        {"id": "o1", "type": "SNIPER_ENTRY", "bbox": [1,2,3,4], "truth_score": 0.9},
        {"id": "o2", "type": "TARGET_ZONE", "bbox": [5,6,7,8], "truth_score": 0.75},
    ]
    # ensure registry dir exists (uses runtime.data_dir); rely on default
    path = persist_market_objects(session_id, objs, chart_transform={"chart_transform_id": "ct_demo", "frame_id": 1})
    assert path.exists()
    entries = load_market_objects(session_id)
    assert isinstance(entries, list)
    assert len(entries) >= 2
    # check fields
    first = entries[-2]
    assert first.get("session_id") == session_id
    assert first.get("overlay") and first.get("overlay").get("id") == "o1"
    assert first.get("lifecycle_state") is not None
