from pathlib import Path
from phoenixguard.vision.adapters import (
    memory_episode_match_to_historical_study,
    a_star_scenario_to_prediction_scenario,
    scenario_paint_output_to_overlay_objects,
)
from phoenixguard.vision.market_registry import merge_market_objects, load_market_objects, query_active_objects


def test_adapters_and_merge_lifecycle(tmp_path: Path):
    # adapters
    mem = {"episode_id": "e1", "side": "SELL", "would_enter_at": {"frame_id": 10}}
    study = memory_episode_match_to_historical_study(mem)
    assert study["study_id"] == "e1"

    ast = {"id": "s1", "side": "BUY", "path": [[0,0],[1,1]], "confidence": 0.42}
    pred = a_star_scenario_to_prediction_scenario(ast)
    assert pred["scenario_id"] == "s1"

    paint = {"overlays": [{"key": "oA", "bbox": [1,2,3,4], "confidence": 0.8}, {"key": "oB", "bbox": [5,6,7,8], "confidence": 0.3}]}
    objs = scenario_paint_output_to_overlay_objects(paint)
    assert len(objs) == 2

    # merge lifecycle
    session = "test-session-lifecycle"
    # write initial
    appended1 = merge_market_objects(session, objs)
    assert appended1 >= 2
    entries = load_market_objects(session)
    assert entries
    # write duplicate overlay to trigger MERGED logic
    duplicate = [dict(objs[0], id=objs[0].get('id'))]
    appended2 = merge_market_objects(session, duplicate)
    assert appended2 >= 1
    # query active with low threshold should include confirmed
    active = query_active_objects(session, min_truth_score=0.0)
    assert isinstance(active, list) and len(active) >= 1