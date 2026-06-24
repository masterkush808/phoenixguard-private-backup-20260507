from __future__ import annotations
from typing import Any

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
from phoenixguard.runtime.observability_v3 import build_model_council_health_from_session
from phoenixguard.vision.market_registry import persist_market_objects
from phoenixguard.vision.v2_overlay_migration import (
    migrate_v2_angle_line,
    migrate_v2_prediction_path,
    migrate_v2_progression_overlay,
    migrate_v2_sniper_overlay,
    migrate_v2_target_zone,
)
from phoenixguard.vision.v3_chart_transform import V3ChartTransform


def _make_chart(path: Path) -> None:
    image = Image.new("RGB", (800, 600), color=(20, 20, 20))
    image.save(path)


def test_v2_sniper_overlay_converts_to_v3_overlay_object() -> None:
    overlay = migrate_v2_sniper_overlay({"id": "s1", "bbox": [1, 2, 3, 4], "confidence": 0.9, "side": "BUY"}, frame_id=7, chart_transform_id="ct_1")
    assert overlay["type"] == "SNIPER_ENTRY"
    assert overlay["source_version"] == "V2_MIGRATED_BEHAVIOUR"
    assert overlay["frame_id"] == 7
    assert overlay["chart_transform_id"] == "ct_1"
    assert overlay["truth_score"] == 0.9


def test_v2_target_zone_converts_to_v3_overlay_object() -> None:
    overlay = migrate_v2_target_zone({"id": "t1", "rect": [5, 6, 7, 8], "confidence": 0.75})
    assert overlay["type"] == "TARGET_ZONE"
    assert overlay["anchor_type"] in {"RECT", "BOX"}


def test_v2_progression_converts_to_v3_historical_progression() -> None:
    overlay = migrate_v2_progression_overlay({"id": "p1", "anchors": [(1, 1), (4, 4)], "confidence": 0.66})
    assert overlay["type"] == "HISTORICAL_PROGRESSION"
    assert overlay["source_version"] == "V2_MIGRATED_BEHAVIOUR"


def test_v2_angle_line_converts_to_v3_angle_vector() -> None:
    overlay = migrate_v2_angle_line({"id": "a1", "bbox": [0, 0, 10, 10], "confidence": 0.81})
    assert overlay["type"] == "ANGLE_VECTOR"
    assert overlay["truth_score"] == 0.81


def test_v2_prediction_path_converts_to_v3_prediction_path() -> None:
    overlay = migrate_v2_prediction_path({"id": "pr1", "bbox": [2, 2, 6, 9], "confidence": 0.51})
    assert overlay["type"] == "PREDICTION_PATH"
    assert "visible_modes" in overlay


def test_v2_adapter_does_not_import_legacy_execution() -> None:
    assert "execution" not in migrate_v2_sniper_overlay.__module__.lower()


def test_model_health_not_0_0_when_visual_packet_exists() -> None:
    payload: dict[str, Any] = {
        "session_id": "health-1",
        "model_council_study_packet": {"packet_id": "pkt_1", "schema_version": "PG_MODEL_COUNCIL_STUDY_V3"},
    }
    health = build_model_council_health_from_session(payload)
    assert health["all_required_models_awake"] is True
    assert len(health["models"]) == 7


def test_visual_health_passes_when_chart_overlay_frontend_aligned(tmp_path: Path) -> None:
    svc = ContinuousWindowTrackerService(root_dir=tmp_path / "wt")
    session_id = "vis-1"
    chart_path = tmp_path / "chart.png"
    _make_chart(chart_path)
    transform = V3ChartTransform.create([800, 600], frame_id=42)
    persist_market_objects(
        session_id,
        [
            {
                "id": "ov1",
                "bbox": [10, 20, 80, 120],
                "confidence": 0.91,
                "frame_id": 42,
                "chart_transform_id": transform.chart_transform_id,
                "coordinate_mode": "CHART_IMAGE_SPACE",
                "source_agent": "test_agent",
                "truth_score": 0.91,
            }
        ],
        chart_transform=transform.as_dict(),
    )
    svc._save_session(
        {
            "session_id": session_id,
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0, 0, 1, 1]},
            "frame_index": 42,
            "last_chart_path": str(chart_path),
            "model_council_study_packet": {"packet_id": "pkt_2", "schema_version": "PG_MODEL_COUNCIL_STUDY_V3"},
        }
    )
    app = create_app(window_tracker_service=svc)
    client = TestClient(app)
    response = client.get(f"/v1/mobile/visual/health/v3?session_id={session_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "PG_VISUAL_HEALTH_V3"
    assert payload["stale"] is False
    assert payload["study_packet"]["exists"] is True
    assert payload["model_health"]["all_required_models_awake"] is True
    assert payload["overlay"]["count"] >= 1
    assert payload["overlay"]["frame_matches_chart_frame"] is True
