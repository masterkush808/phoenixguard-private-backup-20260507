from __future__ import annotations

import json
from pathlib import Path

from phoenixguard.execution.calibration_manifest import (
    NOT_USER_CALIBRATED,
    USER_CALIBRATED,
    build_manifest,
    create_deletion_report,
    list_uncalibrated_artifacts,
    validate_profile_layout,
    write_manifest,
)


def _write_boxes(path: Path, *, buy: bool = True, sell: bool = True, layout_id: str = "desktop") -> None:
    points = {
        "expiry_time_field": {"x": 0.5, "y": 0.2},
        "expiry_plus": {"x": 0.6, "y": 0.2},
        "expiry_minus": {"x": 0.4, "y": 0.2},
        "broker_focus_area": {"x": 0.3, "y": 0.3},
    }
    if buy:
        points["buy_button"] = {"x": 0.8, "y": 0.5}
    if sell:
        points["sell_button"] = {"x": 0.8, "y": 0.6}
    path.write_text(json.dumps({"layout_id": layout_id, **points}), encoding="utf-8")


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    result = validate_profile_layout(tmp_path / "missing.json", "default", "desktop")

    assert result.accepted is False
    assert result.code == "MISSING_MANIFEST"


def test_unknown_profile_is_rejected(tmp_path: Path) -> None:
    boxes = tmp_path / "808_shooter_boxes.json"
    _write_boxes(boxes)
    manifest = build_manifest(boxes, profile_id="known", layout_id="desktop")

    result = validate_profile_layout(manifest, "unknown", "desktop")

    assert result.accepted is False
    assert result.code == "UNKNOWN_PROFILE"


def test_uncalibrated_buy_sell_buttons_are_rejected(tmp_path: Path) -> None:
    boxes = tmp_path / "808_shooter_boxes.json"
    _write_boxes(boxes, buy=False, sell=False)
    manifest = build_manifest(boxes, profile_id="default", layout_id="desktop")

    result = validate_profile_layout(manifest, "default", "desktop")

    assert result.accepted is False
    assert result.code == NOT_USER_CALIBRATED
    assert "buy_button" in result.uncalibrated_targets
    assert "sell_button" in result.uncalibrated_targets


def test_layout_mismatch_is_rejected(tmp_path: Path) -> None:
    boxes = tmp_path / "808_shooter_boxes.json"
    _write_boxes(boxes, layout_id="desktop")
    manifest = build_manifest(boxes, profile_id="default", layout_id="desktop")

    result = validate_profile_layout(manifest, "default", "mobile")

    assert result.accepted is False
    assert result.code == "LAYOUT_MISMATCH"


def test_fully_calibrated_profile_layout_is_accepted_and_writable(tmp_path: Path) -> None:
    boxes = tmp_path / "808_shooter_boxes.json"
    output = tmp_path / "user_calibration_manifest.json"
    _write_boxes(boxes)
    manifest = build_manifest(boxes, profile_id="default", layout_id="desktop")

    write_manifest(manifest, output)
    result = validate_profile_layout(output, "default", "desktop")

    assert result.accepted is True
    assert result.code == USER_CALIBRATED


def test_tampered_calibrated_point_outside_unit_range_is_rejected(tmp_path: Path) -> None:
    boxes = tmp_path / "808_shooter_boxes.json"
    _write_boxes(boxes)
    manifest = build_manifest(boxes, profile_id="default", layout_id="desktop")
    manifest["profiles"]["default"]["layouts"]["desktop"]["required_targets"]["buy_button"]["point"]["x"] = 1.5

    result = validate_profile_layout(manifest, "default", "desktop")

    assert result.accepted is False
    assert result.code == "INVALID_CALIBRATION_POINT"
    assert "buy_button" in result.uncalibrated_targets


def test_amount_and_chart_boxes_are_optional_for_execution(tmp_path: Path) -> None:
    boxes = tmp_path / "808_shooter_boxes.json"
    _write_boxes(boxes)
    payload = json.loads(boxes.read_text(encoding="utf-8"))
    payload.pop("amount_field", None)
    payload.pop("chart_area", None)
    boxes.write_text(json.dumps(payload), encoding="utf-8")
    manifest = build_manifest(boxes, profile_id="default", layout_id="desktop")

    result = validate_profile_layout(manifest, "default", "desktop")

    assert result.accepted is True
    assert "amount_field" not in result.uncalibrated_targets
    assert "chart_area" not in result.uncalibrated_targets


def test_uncalibrated_artifacts_and_deletion_report_do_not_delete(tmp_path: Path) -> None:
    boxes = tmp_path / "808_shooter_boxes.json"
    _write_boxes(boxes, buy=False)
    manifest = build_manifest(boxes, profile_id="default", layout_id="desktop")

    artifacts = list_uncalibrated_artifacts(manifest)
    report = create_deletion_report(artifacts, delete=True)

    assert any(item["artifact_id"] == "buy_button" for item in artifacts)
    assert report["deleted_count"] == 0
    assert report["dry_run"] is False
    assert all(item["deleted"] is False for item in report["artifacts"])
