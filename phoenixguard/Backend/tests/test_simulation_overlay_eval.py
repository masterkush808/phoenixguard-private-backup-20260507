from __future__ import annotations
from typing import Any, Callable, cast

import pytest

from phoenixguard.simulation.overlay_eval.box_metrics import (
    box_metric,
    evaluate_box_metrics,
    temporal_jitter,
)
from phoenixguard.simulation.overlay_eval.label_clutter_metrics import (
    clip_label_box,
    evaluate_label_clutter_metrics,
    label_overlap_count,
)
from phoenixguard.simulation.overlay_eval.zone_snap_metrics import (
    evaluate_zone_snap_metrics,
    score_zone_anchor,
    zone_anchoring_score,
)


def _approx(expected: float) -> object:
    return cast(Callable[[float], object], getattr(pytest, "approx"))(expected)


def test_box_metrics_matches_boxes_and_counts_false_missed_and_clipping() -> None:
    metrics = evaluate_box_metrics(
        [
            {"id": "match", "bbox": [-5, 0, 10, 10]},
            {"id": "false", "bbox": [30, 30, 40, 40]},
        ],
        [
            {"id": "truth_match", "bbox": [0, 0, 10, 10]},
            {"id": "missed", "bbox": [70, 70, 80, 80]},
        ],
        chart_bounds=[0, 0, 100, 100],
        iou_threshold=0.5,
    )

    assert metrics.prediction_count == 2
    assert metrics.truth_count == 2
    assert metrics.matched_count == 1
    assert metrics.false_positive_count == 1
    assert metrics.missed_count == 1
    assert metrics.mean_iou == 1.0
    assert metrics.mean_area_error_ratio == 0.0

    match = metrics.matches[0]
    assert match.prediction_id == "match"
    assert match.truth_id == "truth_match"
    assert match.prediction_clipped is True
    assert match.evaluated_prediction_bbox == (0.0, 0.0, 10.0, 10.0)
    assert match.clipped_area_ratio == _approx(1.0 / 3.0)


def test_box_metric_reports_center_distance_and_temporal_jitter() -> None:
    metric = box_metric(
        [0, 0, 10, 10],
        [3, 4, 13, 14],
        chart_bounds=[0, 0, 30, 40],
    )
    jitter = temporal_jitter(
        [{"id": "stable", "bbox": [0, 0, 10, 10]}],
        [{"id": "stable", "bbox": [3, 4, 13, 14]}],
        chart_bounds=[0, 0, 30, 40],
    )

    assert metric is not None
    assert metric.center_distance_px == 5.0
    assert metric.center_distance_norm == 0.1
    assert jitter.object_count == 1
    assert jitter.mean_center_shift_px == 5.0
    assert jitter.max_center_shift_norm == 0.1


def test_zone_snap_metrics_score_anchor_quality_and_clipping() -> None:
    zones: list[dict[str, Any]] = [
        {"id": "anchored", "bbox": [10, 90, 60, 110], "line_y": 100},
        {"id": "loose", "bbox": [70, 90, 120, 110], "line_y": 125},
        {"id": "clipped", "bbox": [-10, 40, 20, 60], "line_y": 50},
    ]

    metrics = evaluate_zone_snap_metrics(
        zones,
        chart_bounds=[0, 0, 160, 120],
        minimum_score=0.72,
    )

    assert metrics.zone_count == 3
    assert metrics.valid_zone_count == 3
    assert metrics.anchored_count == 2
    assert metrics.unanchored_count == 1
    assert metrics.clipped_count == 1
    assert metrics.mean_anchoring_score == 0.75
    assert metrics.min_anchoring_score == 0.25
    assert metrics.metrics[2].clipped_area_ratio == _approx(1.0 / 3.0)
    assert zone_anchoring_score(zones[0], chart_bounds=[0, 0, 160, 120]) == 1.0


def test_zone_anchor_can_score_external_touch_points() -> None:
    metric = score_zone_anchor(
        {"id": "touch_zone", "bbox": [20, 40, 80, 60]},
        anchor_points=[[50, 50], [200, 50]],
    )

    assert metric.anchor_count == 2
    assert metric.anchoring_score == 1.0
    assert metric.best_anchor_x == 50.0
    assert metric.best_anchor_y == 50.0


def test_label_clutter_counts_overlaps_and_clipped_labels() -> None:
    labels: list[dict[str, Any]] = [
        {"id": "a", "bbox": [0, 0, 10, 10]},
        {"id": "b", "bbox": [5, 5, 15, 15]},
        {"id": "c", "bbox": [30, 30, 45, 45]},
        {"id": "offscreen", "bbox": [50, 50, 55, 55]},
        {"id": "invalid", "bbox": [0, 0, 0, 10]},
    ]

    metrics = evaluate_label_clutter_metrics(labels, chart_bounds=[0, 0, 40, 40])

    assert metrics.label_count == 5
    assert metrics.valid_label_count == 4
    assert metrics.visible_label_count == 3
    assert metrics.invalid_label_count == 1
    assert metrics.clipped_count == 2
    assert metrics.overlap_count == 1
    assert metrics.overlaps[0].first_id == "a"
    assert metrics.overlaps[0].second_id == "b"
    assert metrics.overlaps[0].overlap_ratio == 0.25
    assert metrics.mean_clipped_area_ratio == _approx((0.0 + 0.0 + (125.0 / 225.0) + 1.0) / 4.0)
    assert label_overlap_count(labels, chart_bounds=[0, 0, 40, 40]) == 1
    assert clip_label_box(labels[2], [0, 0, 40, 40]) == (30.0, 30.0, 40.0, 40.0)
