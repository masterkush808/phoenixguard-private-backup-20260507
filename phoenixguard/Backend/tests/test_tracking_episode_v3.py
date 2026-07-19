from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
import pytest

from phoenixguard.mobile_api import app as mobile_app
from phoenixguard.mobile_api import live_state_v3
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
from phoenixguard.mobile_api.operator_workspace_v1 import (
    build_operator_workspace_v1,
    project_public_tracking_episode_v1,
)
from phoenixguard.tracking.tracking_episode_v3 import (
    TRACKING_EPISODE_HORIZON,
    TrackingEpisodeReadinessError,
    TrackingEpisodeStateError,
    advance_tracking_episode_v1,
    default_tracking_episode_v1,
    reset_tracking_episode_v1,
    start_tracking_episode_v1,
    stop_tracking_episode_v1,
    tracking_episode_readiness_v1,
    update_tracking_episode_history_v1,
)


def _forecast_path(side: str = "BUY") -> list[dict[str, Any]]:
    return [
        {
            "step": step,
            "side": side,
            "expected_close_norm": round(0.50 + (step * 0.01), 6),
            "confidence": 0.82,
        }
        for step in range(1, TRACKING_EPISODE_HORIZON + 1)
    ]


def _scene(key: str = "closed-0", sequence: int = 10, side: str = "BUY") -> dict[str, Any]:
    candles = [
        {
            "step": step,
            "movement_side": side,
            "close_y_norm": round(0.50 - (step * 0.01), 6),
        }
        for step in range(1, TRACKING_EPISODE_HORIZON + 1)
    ]
    main_points = [
        [round(0.55 + step * 0.02, 6), round(0.50 - step * 0.01, 6)]
        for step in range(TRACKING_EPISODE_HORIZON + 1)
    ]
    alternate_points = [
        [round(0.55 + step * 0.02, 6), round(0.50 + step * 0.008, 6)]
        for step in range(TRACKING_EPISODE_HORIZON + 1)
    ]
    def scenario_candles(
        points: list[list[float]],
        direction: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "step": step,
                "open_y_norm": points[step - 1][1],
                "close_y_norm": points[step][1],
                "movement_side": direction,
            }
            for step in range(1, TRACKING_EPISODE_HORIZON + 1)
        ]
    return {
        "pair": "EURUSD_OTC",
        "timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
        "closed_candle_key": key,
        "closed_candle_sequence": sequence,
        "source_image_size": [1000, 500],
        "closed_candle_identity_state": {
            "event_key": key,
            "event_sequence": sequence,
            "latest_closed": {"track_id": str(sequence), "side": side},
            "forming": {
                "track_id": f"forming-{sequence}",
                "side": side,
                "open_y": 248,
                "close_y": 244,
                "top_y": 240,
                "bottom_y": 252,
            },
            "median_range": 10.0,
        },
        "path_side": side,
        "forecast_candles": candles,
        "forecast_anchor": {
            "target_scale_norm": 0.02,
            "event_step_x_norm": 0.02,
        },
        "forecast_scenarios": [
            {
                "role": "base",
                "side": side,
                "selected": True,
                "probability": 0.62,
                "line_points": main_points,
                "forecast_candles": scenario_candles(main_points, side),
            },
            {
                "role": "alternate",
                "side": "SELL" if side == "BUY" else "BUY",
                "selected": False,
                "probability": 0.38,
                "line_points": alternate_points,
                "forecast_candles": scenario_candles(
                    alternate_points,
                    "SELL" if side == "BUY" else "BUY",
                ),
            },
        ],
    }


def _ready_session(
    *,
    key: str = "closed-0",
    sequence: int = 10,
    side: str = "BUY",
) -> dict[str, Any]:
    scene = _scene(key, sequence, side)
    return {
        "session_id": "episode-test",
        "market": "EURUSD_OTC",
        "tracking_enabled": True,
        "frame_index": sequence,
        "display_frame_id": sequence,
        "model_vote_frame_id": sequence,
        "model_capture_epoch": 1_780_000_000.0 + sequence,
        "last_capture_epoch": 1_780_000_000.0 + sequence,
        "last_capture_at": f"2026-07-18T00:{sequence:02d}:00+00:00",
        "source_capture_id": f"capture-{sequence}",
        "last_study_surface_signature": f"surface-{sequence}",
        "frame_bundle_complete_v3": True,
        "manual_focus_region": {
            "enabled": True,
            "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
        },
        "forecast_snapshot_v3": {
            "pair": "EURUSD_OTC",
            "timeframe": "M5",
            "market_identity_confirmed": True,
            "timeframe_identity_confirmed": True,
            "scene_forecast_contribution": scene,
            "lstm_contribution": {
                "side": side,
                "trajectory_mode": side,
                "horizon_steps": TRACKING_EPISODE_HORIZON,
                "forecast_path": _forecast_path(side),
            },
        },
        "latest_signal": {
            "market": "EURUSD_OTC",
            "focus_timeframe": "M5",
            "action": side,
            "execution_action": "HOLD",
            "execution_permission": "WAIT",
            "effective_confidence": 0.82,
            "summary": "Baseline decision",
            "scene_forecast_contribution": scene,
        },
        "tracking_summary": {
            "detected_market": "EURUSD_OTC",
            "detected_timeframe": "M5",
            "tracked_candles": [
                {
                    "track_id": str(sequence),
                    "is_closed": True,
                    "direction": side,
                    "close_norm": round(0.50 + sequence * 0.001, 6),
                    "price_proxy": round(0.50 + sequence * 0.001, 6),
                },
                {
                    "track_id": f"forming-{sequence}",
                    "is_closed": False,
                    "direction": side,
                    "open_y": 248,
                    "close_y": 244,
                }
            ],
        },
        "signal_thesis_v3": {
            "thesis_id": "thesis-1",
            "side": side,
            "state": "ACTIVE",
            "summary": "Baseline thesis",
        },
        "execution_opportunity_window_v3": {
            "opportunity_id": "window-1",
            "status": "WATCHING",
        },
        "memory_projection_predict": {
            "status": "ready",
            "side": side,
            "forecast_path": _forecast_path(side),
        },
        "memory_projection_future": {},
        "memory_projection_active_mode": "predict",
    }


def _next_closed_event(
    session: dict[str, Any],
    *,
    step: int,
    side: str = "BUY",
) -> dict[str, Any]:
    updated = deepcopy(session)
    sequence = 10 + step
    scene = _scene(f"closed-{step}", sequence, side)
    snapshot = cast(dict[str, Any], updated["forecast_snapshot_v3"])
    snapshot["scene_forecast_contribution"] = scene
    signal = cast(dict[str, Any], updated["latest_signal"])
    signal["scene_forecast_contribution"] = scene
    signal["action"] = side
    tracking = cast(dict[str, Any], updated["tracking_summary"])
    tracking["tracked_candles"] = [
        {
            "track_id": str(sequence),
            "is_closed": True,
            "direction": side,
            "close_norm": round(0.50 + step * 0.01, 6),
            "price_proxy": round(0.50 + step * 0.01, 6),
        }
    ]
    updated["frame_index"] = sequence
    updated["display_frame_id"] = sequence
    updated["model_vote_frame_id"] = sequence
    updated["source_capture_id"] = f"capture-{sequence}"
    updated["last_study_surface_signature"] = f"surface-{sequence}"
    return updated


def _recovered_gap_session(session: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(session)
    scene = _scene("closed-3", 13, "BUY")
    scene["closed_candle_transition_reason"] = (
        "VISUAL_CLOSED_CANDLE_GAP_REACQUIRED"
    )
    scene["closed_candle_identity_state"].update(
        {
            "transition_count": 3,
            "reacquisition": {
                "status": "CONFIRMED",
                "reason": "FORMER_LIVE_BAR_FOUND_IN_CONTIGUOUS_CLOSED_HISTORY",
                "confirmed_closed_count": 3,
            },
            "confirmed_event_batch": [
                {
                    "closed_candle_key": f"closed-{step}",
                    "closed_candle_sequence": 10 + step,
                    "observation": {
                        "track_id": str(10 + step),
                        "side": side,
                        "price_proxy": 0.50 + step * 0.01,
                    },
                    "confirmation_reason": "VISUAL_CLOSED_CANDLE_GAP_REACQUIRED",
                    "reacquired": True,
                }
                for step, side in ((1, "BUY"), (2, "SELL"), (3, "BUY"))
            ],
        }
    )
    cast(dict[str, Any], updated["forecast_snapshot_v3"])[
        "scene_forecast_contribution"
    ] = scene
    cast(dict[str, Any], updated["latest_signal"])[
        "scene_forecast_contribution"
    ] = scene
    cast(dict[str, Any], updated["tracking_summary"])["tracked_candles"] = [
        {
            "track_id": "13",
            "is_closed": True,
            "direction": "BUY",
            "close_norm": 0.53,
        }
    ]
    updated["frame_index"] = 13
    updated["display_frame_id"] = 13
    updated["model_vote_frame_id"] = 13
    updated["last_capture_at"] = "2026-07-18T00:15:00+00:00"
    return updated


def test_tracking_episode_requires_a_complete_event_locked_baseline() -> None:
    readiness = tracking_episode_readiness_v1({"session_id": "not-ready"})
    paused_session = _ready_session()
    paused_session["tracking_enabled"] = False
    paused_readiness = tracking_episode_readiness_v1(paused_session)

    assert readiness["ready"] is False
    assert readiness["reasons"]
    assert paused_readiness["ready"] is False
    assert "Wait for live chart tracking to be running." in paused_readiness["reasons"]
    with pytest.raises(TrackingEpisodeReadinessError):
        start_tracking_episode_v1(
            default_tracking_episode_v1(session_id="not-ready"),
            {"session_id": "not-ready"},
            episode_id="episode-not-ready",
            now_iso="2026-07-18T00:00:00+00:00",
        )


def test_readiness_derives_transform_from_valid_scene_when_anchor_metadata_is_null() -> None:
    session = _ready_session()
    scene = cast(
        dict[str, Any],
        cast(dict[str, Any], session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    scene["forecast_anchor"] = {
        "target_scale_norm": None,
        "event_step_x_norm": None,
    }

    readiness = tracking_episode_readiness_v1(session)
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-derived-transform",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    transform = cast(
        dict[str, Any],
        cast(dict[str, Any], started["path_comparison"])["transform_contract"],
    )
    advanced = advance_tracking_episode_v1(
        started,
        _next_closed_event(session, step=1),
        now_iso="2026-07-18T00:05:00+00:00",
    )

    assert readiness["ready"] is True
    assert transform["status"] == "LOCKED"
    assert transform["target_scale_norm"] == 0.02
    assert transform["target_scale_source"] == "MEDIAN_CANDLE_RANGE"
    assert transform["event_step_x_norm"] == 0.02
    assert transform["event_step_x_source"] == "SELECTED_PATH_GEOMETRY"
    assert advanced["events"][0]["geometry_status"] == "AVAILABLE"
    assert advanced["events"][0]["path_fit_by_id"]["PATH_A"]["status"] == (
        "MEASURED"
    )


def test_readiness_blocks_when_transform_scale_cannot_be_derived() -> None:
    session = _ready_session()
    scene = cast(
        dict[str, Any],
        cast(dict[str, Any], session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    scene["forecast_anchor"] = {
        "target_scale_norm": None,
        "event_step_x_norm": None,
    }
    cast(dict[str, Any], scene["closed_candle_identity_state"]).pop(
        "median_range"
    )
    selected = next(
        scenario
        for scenario in cast(list[dict[str, Any]], scene["forecast_scenarios"])
        if scenario.get("selected") is True
    )
    flat_points = [
        [round(0.55 + step * 0.02, 6), 0.5]
        for step in range(TRACKING_EPISODE_HORIZON + 1)
    ]
    selected["line_points"] = flat_points
    selected["forecast_candles"] = [
        {
            "step": step,
            "open_y_norm": 0.5,
            "close_y_norm": 0.5,
            "movement_side": "HOLD",
        }
        for step in range(1, TRACKING_EPISODE_HORIZON + 1)
    ]

    readiness = tracking_episode_readiness_v1(session)

    assert readiness["ready"] is False
    assert (
        "Wait for stable normalized chart geometry before starting tracking."
        in readiness["reasons"]
    )
    with pytest.raises(TrackingEpisodeReadinessError):
        start_tracking_episode_v1(
            {},
            session,
            episode_id="episode-missing-transform",
            now_iso="2026-07-18T00:00:00+00:00",
        )


def test_tracking_episode_is_idempotent_and_advances_only_on_new_closed_events() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        default_tracking_episode_v1(session_id="episode-test"),
        session,
        episode_id="episode-fixed",
        now_iso="2026-07-18T00:00:00+00:00",
    )

    duplicate_start = start_tracking_episode_v1(
        started,
        session,
        episode_id="episode-must-not-replace",
        now_iso="2026-07-18T00:00:01+00:00",
    )
    same_forming_candle = advance_tracking_episode_v1(
        started,
        session,
        now_iso="2026-07-18T00:00:02+00:00",
    )
    first_event = advance_tracking_episode_v1(
        started,
        _next_closed_event(session, step=1),
        now_iso="2026-07-18T00:05:00+00:00",
    )

    assert duplicate_start == started
    assert same_forming_candle["event_cursor"] == started["event_cursor"]
    assert same_forming_candle["events"] == started["events"]
    assert same_forming_candle["revision"] == started["revision"]
    assert same_forming_candle["baseline_forecasts"] == started["baseline_forecasts"]
    assert started["anchor"]["closed_candle_identity_state"] == _scene()[
        "closed_candle_identity_state"
    ]
    assert len(started["baseline_forecasts"]["lstm"]["forecast_path"]) == 12
    assert first_event["event_cursor"] == 1
    assert first_event["events"][0]["event_id"] == "episode-fixed:E1"
    assert first_event["events"][0]["direction_agreement"] is True
    assert first_event["baseline_forecasts"] == started["baseline_forecasts"]
    assert first_event["committed_plan"] == started["committed_plan"]


def test_start_freezes_latest_closed_candle_and_two_real_distinct_paths() -> None:
    session = _ready_session()
    scenarios = cast(
        list[dict[str, Any]],
        cast(dict[str, Any], session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ]["forecast_scenarios"],
    )
    scenarios[1]["side"] = "BUY"
    for candle in cast(list[dict[str, Any]], scenarios[1]["forecast_candles"]):
        candle["movement_side"] = "BUY"

    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-two-paths",
        now_iso="2026-07-18T00:00:00+00:00",
    )

    paths = cast(list[dict[str, Any]], started["path_comparison"]["paths"])
    assert started["anchor"]["closed_candle_key"] == "closed-0"
    assert started["anchor"]["closed_candle_sequence"] == 10
    assert started["anchor"]["starting_candle"]["chart_close_norm"] == 0.49
    assert started["path_comparison"]["forming_at_start"] == {
        "status": "OBSERVED",
        "label": "Candle forming when tracking started",
        "direction": "BUY",
        "open_level": 0.496,
        "current_level": 0.488,
    }
    assert [path["id"] for path in paths] == ["PATH_A", "PATH_B"]
    assert [path["label"] for path in paths] == [
        "Main forecast",
        "Alternative forecast",
    ]
    assert all(len(path["steps"]) == TRACKING_EPISODE_HORIZON for path in paths)
    assert paths[0]["direction"] == paths[1]["direction"] == "BUY"
    assert paths[0]["steps"] != paths[1]["steps"]
    assert started["path_comparison"]["verdict"] == "WAITING"
    assert started["permission"]["entry_permitted"] is False
    assert started["path_comparison"]["entry_thesis"] == {
        "status": "DIRECTIONAL",
        "label": "Entry idea at start",
        "summary": "Baseline thesis",
        "direction": "BUY",
    }


def test_forming_anchor_falls_back_to_scene_identity_record() -> None:
    session = _ready_session()
    tracked = cast(
        list[dict[str, Any]],
        cast(dict[str, Any], session["tracking_summary"])["tracked_candles"],
    )
    cast(dict[str, Any], session["tracking_summary"])["tracked_candles"] = [
        row for row in tracked if row.get("is_closed") is not False
    ]

    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-forming-fallback",
        now_iso="2026-07-18T00:00:00+00:00",
    )

    assert started["path_comparison"]["forming_at_start"] == {
        "status": "OBSERVED",
        "label": "Candle forming when tracking started",
        "direction": "BUY",
        "open_level": 0.496,
        "current_level": 0.488,
    }


def test_trade_permission_requires_canonical_permission_valid_packet_and_allowance() -> None:
    blocked_session = _ready_session()
    blocked_latest = cast(dict[str, Any], blocked_session["latest_signal"])
    blocked_latest.pop("execution_permission")
    blocked_latest["entry_state"] = "ENTER_NOW"
    blocked = start_tracking_episode_v1(
        {},
        blocked_session,
        episode_id="episode-permission-blocked",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    assert blocked["path_comparison"]["trade_permission"]["status"] == "WAIT"
    assert blocked["permission"]["entry_permitted"] is False

    permitted_session = _ready_session()
    permitted_latest = cast(dict[str, Any], permitted_session["latest_signal"])
    permitted_latest["execution_permission"] = "PERMITTED"
    permitted_session["execution_packet"] = {
        "packet_validation": {"valid": True},
        "allowance_package": {"allowed": True},
    }
    permitted = start_tracking_episode_v1(
        {},
        permitted_session,
        episode_id="episode-permission-validated",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    assert permitted["path_comparison"]["trade_permission"]["status"] == (
        "PERMITTED"
    )
    assert permitted["permission"]["entry_permitted"] is True


def test_confirmed_normalized_closes_explicitly_favor_closest_saved_path() -> None:
    session = _ready_session()
    episode = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-path-fit",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    frozen_forecasts = deepcopy(episode["baseline_forecasts"])
    for step in range(1, 4):
        episode = advance_tracking_episode_v1(
            episode,
            _next_closed_event(session, step=step),
            now_iso=f"2026-07-18T00:{step * 5:02d}:00+00:00",
        )

    first = cast(dict[str, Any], episode["events"][0])
    assert first["actual_block"]["close_norm"] == 0.51
    # The explicit price_proxy contract becomes chart Y: 1 - 0.51 = 0.49.
    assert first["actual_block"]["chart_close_norm"] == 0.49
    assert first["path_fit_by_id"]["PATH_A"]["status"] == "MEASURED"
    assert first["path_fit_by_id"]["PATH_A"]["error"] == 0.0
    assert first["path_fit_by_id"]["PATH_B"]["error"] == 0.018
    assert first["favored_path_id"] == "PATH_A"
    assert episode["path_comparison"]["verdict"] == "PATH_A"
    assert episode["path_comparison"]["favored_path_id"] == "PATH_A"
    assert episode["baseline_forecasts"] == frozen_forecasts

    public = project_public_tracking_episode_v1({"tracking_episode": episode})
    comparison = cast(dict[str, Any], public["path_comparison"])
    public_event = cast(list[dict[str, Any]], public["events"])[0]
    assert comparison["schema_version"] == "PG_TRACKING_PATH_COMPARISON_PUBLIC_V1"
    assert comparison["verdict"] == "PATH_A"
    assert comparison["favored_path_id"] == "PATH_A"
    assert len(comparison["paths"]) == 2
    assert comparison["anchor"]["label"] == "Latest completed candle"
    assert comparison["forming_at_start"]["label"] == (
        "Candle forming when tracking started"
    )
    assert comparison["forecast_bias"]["direction"] == "BUY"
    assert comparison["entry_thesis"]["summary"] == "Baseline thesis"
    assert comparison["trade_permission"]["status"] == "WAIT"
    assert comparison["continuity"]["state"] == "LIVE"
    assert public_event["path_fit_by_id"]["PATH_A"] == {
        "status": "MEASURED",
        "direction_agreement": True,
    }
    assert "fit" not in public_event["path_fit_by_id"]["PATH_A"]
    assert "error" not in public_event["path_fit_by_id"]["PATH_A"]
    assert "distance" not in public_event["entry_location_progress"]
    assert public_event["favored_path_id"] == "PATH_A"


def test_scene_paths_reject_one_step_outlier_and_non_increasing_x() -> None:
    outlier_session = _ready_session()
    outlier_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], outlier_session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    outlier_scenarios = cast(
        list[dict[str, Any]],
        outlier_scene["forecast_scenarios"],
    )
    outlier_scenarios[1]["line_points"] = deepcopy(
        outlier_scenarios[0]["line_points"]
    )
    outlier_scenarios[1]["forecast_candles"] = deepcopy(
        outlier_scenarios[0]["forecast_candles"]
    )
    outlier_points = cast(list[list[float]], outlier_scenarios[1]["line_points"])
    outlier_points[-1][1] = round(outlier_points[-1][1] + 0.05, 6)
    outlier_candles = cast(
        list[dict[str, Any]],
        outlier_scenarios[1]["forecast_candles"],
    )
    outlier_candles[-1]["close_y_norm"] = outlier_points[-1][1]

    outlier_readiness = tracking_episode_readiness_v1(outlier_session)
    assert outlier_readiness["ready"] is False
    assert outlier_readiness["path_count"] == 0

    invalid_x_session = _ready_session()
    invalid_x_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], invalid_x_session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    invalid_x_scenarios = cast(
        list[dict[str, Any]],
        invalid_x_scene["forecast_scenarios"],
    )
    invalid_points = cast(list[list[float]], invalid_x_scenarios[1]["line_points"])
    invalid_points[6][0] = invalid_points[5][0]

    invalid_readiness = tracking_episode_readiness_v1(invalid_x_session)
    assert invalid_readiness["ready"] is False
    assert invalid_readiness["path_count"] == 0

    anchor_session = _ready_session()
    anchor_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], anchor_session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    anchor_scenarios = cast(
        list[dict[str, Any]],
        anchor_scene["forecast_scenarios"],
    )
    anchor_points = cast(list[list[float]], anchor_scenarios[1]["line_points"])
    anchor_points[0][1] = round(anchor_points[0][1] + 0.01, 6)
    anchor_readiness = tracking_episode_readiness_v1(anchor_session)
    assert anchor_readiness["ready"] is False
    assert anchor_readiness["path_count"] == 0


def test_path_fit_is_explicitly_unknown_without_normalized_candle_geometry() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-path-unknown",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    no_geometry = _next_closed_event(session, step=1)
    tracked = cast(
        list[dict[str, Any]],
        cast(dict[str, Any], no_geometry["tracking_summary"])["tracked_candles"],
    )
    tracked[0].pop("close_norm")
    tracked[0].pop("price_proxy")

    advanced = advance_tracking_episode_v1(
        started,
        no_geometry,
        now_iso="2026-07-18T00:05:00+00:00",
    )
    event = cast(dict[str, Any], advanced["events"][0])

    assert event["path_fit_by_id"] == {
        "PATH_A": {"status": "UNKNOWN"},
        "PATH_B": {"status": "UNKNOWN"},
    }
    public_event = cast(
        list[dict[str, Any]],
        project_public_tracking_episode_v1(
            {"tracking_episode": advanced}
        )["events"],
    )[0]
    assert public_event["path_fit_by_id"] == {
        "PATH_A": {
            "status": "UNKNOWN",
            "direction_agreement": None,
        },
        "PATH_B": {
            "status": "UNKNOWN",
            "direction_agreement": None,
        },
    }
    assert public_event["favored_path_id"] == ""
    assert advanced["path_comparison"]["verdict"] == "GEOMETRY_UNAVAILABLE"


def test_path_b_is_farthest_scene_alternative_and_never_borrowed_from_lstm() -> None:
    session = _ready_session()
    scene = cast(
        dict[str, Any],
        cast(dict[str, Any], session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    scenarios = cast(list[dict[str, Any]], scene["forecast_scenarios"])
    far_points = [
        [round(0.55 + step * 0.02, 6), round(0.50 + step * 0.03, 6)]
        for step in range(TRACKING_EPISODE_HORIZON + 1)
    ]
    scenarios.append(
        {
            "role": "far",
            "side": "SELL",
            "selected": False,
            "probability": 0.01,
            "line_points": far_points,
            "forecast_candles": [
                {
                    "step": step,
                    "open_y_norm": far_points[step - 1][1],
                    "close_y_norm": far_points[step][1],
                    "movement_side": "SELL",
                }
                for step in range(1, TRACKING_EPISODE_HORIZON + 1)
            ],
        }
    )

    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-farthest-scene",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    paths = cast(list[dict[str, Any]], started["path_comparison"]["paths"])
    assert paths[1]["steps"][-1]["close_level"] == 0.86

    overlap_session = _ready_session()
    overlap_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], overlap_session["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    overlap_scenarios = cast(
        list[dict[str, Any]],
        overlap_scene["forecast_scenarios"],
    )
    overlap_scenarios[1]["line_points"] = deepcopy(
        overlap_scenarios[0]["line_points"]
    )
    overlap_scenarios[1]["forecast_candles"] = deepcopy(
        overlap_scenarios[0]["forecast_candles"]
    )
    lstm = cast(
        dict[str, Any],
        cast(dict[str, Any], overlap_session["forecast_snapshot_v3"])[
            "lstm_contribution"
        ],
    )
    lstm["forecast_scenarios"] = scenarios

    readiness = tracking_episode_readiness_v1(overlap_session)
    assert readiness["ready"] is False
    assert readiness["path_count"] == 0
    assert "Scene forecast paths" in cast(list[str], readiness["reasons"])[-1]


def test_raw_pixel_body_is_normalized_by_scene_height_for_path_fit() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-pixel-fit",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    observed = _next_closed_event(session, step=1)
    candle = cast(
        list[dict[str, Any]],
        cast(dict[str, Any], observed["tracking_summary"])["tracked_candles"],
    )[0]
    candle.pop("price_proxy")
    candle.update(
        {
            "open_y": 250,
            "close_y": 245,
            "top_y": 240,
            "bottom_y": 255,
        }
    )

    advanced = advance_tracking_episode_v1(
        started,
        observed,
        now_iso="2026-07-18T00:05:00+00:00",
    )
    event = cast(dict[str, Any], advanced["events"][0])
    actual = cast(dict[str, Any], event["actual_block"])

    assert actual["chart_open_norm"] == 0.5
    assert actual["chart_close_norm"] == 0.49
    assert actual["chart_top_norm"] == 0.48
    assert actual["chart_bottom_norm"] == 0.51
    assert event["path_fit_by_id"]["PATH_A"]["error"] == 0.0


def test_transform_change_makes_path_fit_and_public_progress_unknown() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-transform-change",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    changed = _next_closed_event(session, step=1)
    changed_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], changed["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    changed_scene["source_image_size"] = [1000, 600]
    cast(dict[str, Any], changed["latest_signal"])[
        "scene_forecast_contribution"
    ] = changed_scene

    advanced = advance_tracking_episode_v1(
        started,
        changed,
        now_iso="2026-07-18T00:05:00+00:00",
    )
    public = project_public_tracking_episode_v1(
        {"tracking_episode": advanced}
    )
    event = cast(list[dict[str, Any]], public["events"])[0]
    comparison = cast(dict[str, Any], public["path_comparison"])

    assert event["observed_close_level"] is None
    assert event["path_fit_by_id"]["PATH_A"]["status"] == "UNKNOWN"
    assert event["entry_location_progress"] == {"status": "UNKNOWN"}
    assert comparison["verdict"] == "GEOMETRY_UNAVAILABLE"
    assert comparison["geometry"]["status"] == "UNAVAILABLE"
    serialized = json.dumps(comparison).lower()
    assert "source_width" not in serialized
    assert "surface_transform_identity" not in serialized


def test_entry_location_uses_matching_thesis_and_tracks_normalized_progress() -> None:
    session = _ready_session()
    session["signal_thesis_v3"] = {
        "thesis_id": "thesis-entry-zone",
        "side": "BUY",
        "summary": "Resolved buy thesis",
        "chart_height_proxy": 500,
        "entry_zone": {
            "key": "buy-zone-1",
            "anchor_price_band": {"top_y": 200, "bottom_y": 300},
        },
    }
    session["execution_opportunity_window_v3"] = {
        "side": "SELL",
        "symbol": "EURUSD_OTC",
        "timeframe": "M5",
        "state": "OPEN",
        "integrity_valid": True,
        "lineage_rejected": False,
        "valid_until_epoch": 1_900_000_000.0,
        "entry_location_guidance_v3": {
            "side": "SELL",
            "preferred_price_location": "HIGHER_PRICE",
            "message": "Stale sell guidance",
        },
    }
    cast(dict[str, Any], session["latest_signal"])["execution_timing"] = {
        "side": "SELL",
        "entry_area_zone": {
            "key": "wrong-sell-zone",
            "anchor_price_band": {"top_y": 20, "bottom_y": 40},
        },
    }
    episode = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-entry-progress",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    location = cast(dict[str, Any], episode["path_comparison"]["entry_location"])
    assert location["status"] == "TRACKING"
    assert location["direction"] == "BUY"
    assert location["preferred_location"] == "LOWER_PRICE"
    assert location["top_level"] == 0.4
    assert location["bottom_level"] == 0.6
    assert "Stale sell guidance" not in location["summary"]

    for step, proxy in ((1, 0.30), (2, 0.35), (3, 0.20)):
        observed = _next_closed_event(session, step=step)
        candle = cast(
            list[dict[str, Any]],
            cast(dict[str, Any], observed["tracking_summary"])["tracked_candles"],
        )[0]
        candle["price_proxy"] = proxy
        episode = advance_tracking_episode_v1(
            episode,
            observed,
            now_iso=f"2026-07-18T00:{step * 5:02d}:00+00:00",
        )

    public = project_public_tracking_episode_v1(
        {"tracking_episode": episode}
    )
    events = cast(list[dict[str, Any]], public["events"])
    public_location = cast(
        dict[str, Any],
        cast(dict[str, Any], public["path_comparison"])["entry_location"],
    )
    assert [event["entry_location_progress"]["status"] for event in events] == [
        "OUTSIDE",
        "APPROACHING",
        "MOVED_AWAY",
    ]
    assert events[0]["observed_close_level"] == 0.7
    assert public_location["progress"]["status"] == "MOVED_AWAY"
    assert "distance" not in public_location["progress"]
    assert public_location["top_level"] == 0.4
    assert public_location["bottom_level"] == 0.6


def test_nested_entry_thesis_stays_separate_from_bias_and_permission() -> None:
    session = _ready_session()
    session.pop("signal_thesis_v3")
    latest = cast(dict[str, Any], session["latest_signal"])
    latest["signal_thesis_v3"] = {
        "side": "SELL",
        "summary": "Wait for the higher sell entry area.",
    }
    latest["execution_permission"] = "WAIT"

    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-separated-thesis",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    comparison = cast(dict[str, Any], started["path_comparison"])

    assert comparison["forecast_bias"]["direction"] == "BUY"
    assert comparison["entry_thesis"]["direction"] == "SELL"
    assert comparison["entry_thesis"]["summary"] == (
        "Wait for the higher sell entry area."
    )
    assert comparison["trade_permission"]["status"] == "WAIT"
    assert started["committed_plan"]["signal_thesis"] == {
        "side": "SELL",
        "summary": "Wait for the higher sell entry area.",
    }


def test_episode_reports_neither_fit_and_geometry_unavailable_without_guessing() -> None:
    session = _ready_session()
    neither = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-neither-fit",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    unavailable = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-no-geometry",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    for step in range(1, 4):
        far = _next_closed_event(session, step=step)
        far_candle = cast(
            list[dict[str, Any]],
            cast(dict[str, Any], far["tracking_summary"])["tracked_candles"],
        )[0]
        far_candle["price_proxy"] = 0.10
        neither = advance_tracking_episode_v1(
            neither,
            far,
            now_iso=f"2026-07-18T00:{step * 5:02d}:00+00:00",
        )

        missing = _next_closed_event(session, step=step)
        missing_candle = cast(
            list[dict[str, Any]],
            cast(dict[str, Any], missing["tracking_summary"])["tracked_candles"],
        )[0]
        missing_candle.pop("price_proxy")
        missing_candle.pop("close_norm")
        unavailable = advance_tracking_episode_v1(
            unavailable,
            missing,
            now_iso=f"2026-07-18T00:{step * 5:02d}:00+00:00",
        )

    assert neither["path_comparison"]["verdict"] == "NEITHER_PATH_FITS"
    assert neither["path_comparison"].get("favored_path_id", "") == ""
    assert unavailable["path_comparison"]["verdict"] == "GEOMETRY_UNAVAILABLE"
    assert unavailable["path_comparison"].get("favored_path_id", "") == ""


def test_tracking_episode_consumes_reacquired_actual_batch_once_without_reforecasting() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-recovered-gap",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    gap_session = _recovered_gap_session(session)

    recovered = advance_tracking_episode_v1(
        started,
        gap_session,
        now_iso="2026-07-18T00:15:00+00:00",
    )
    replay = advance_tracking_episode_v1(
        recovered,
        gap_session,
        now_iso="2026-07-18T00:15:30+00:00",
    )

    assert recovered["event_cursor"] == 3
    assert [row["event_id"] for row in recovered["events"]] == [
        "episode-recovered-gap:E1",
        "episode-recovered-gap:E2",
        "episode-recovered-gap:E3",
    ]
    assert [row["observation_kind"] for row in recovered["events"]] == [
        "REACQUIRED_HISTORY",
        "REACQUIRED_HISTORY",
        "REACQUIRED_HISTORY",
    ]
    assert [row["actual_block"]["side"] for row in recovered["events"]] == [
        "BUY",
        "SELL",
        "BUY",
    ]
    assert [row.get("direction_agreement") for row in recovered["events"]] == [
        True,
        False,
        True,
    ]
    assert recovered["baseline_forecasts"] == started["baseline_forecasts"]
    assert recovered["committed_plan"] == started["committed_plan"]
    assert recovered["observation_state"]["status"] == "LIVE"
    assert recovered["observation_state"]["confirmed_event_count"] == 3
    assert replay["events"] == recovered["events"]
    assert replay["event_cursor"] == recovered["event_cursor"]
    assert replay["revision"] == recovered["revision"]
    assert replay["baseline_forecasts"] == recovered["baseline_forecasts"]


def test_tracking_episode_consumes_only_first_twelve_of_twenty_four_reacquired_events() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-long-recovered-gap",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    recovered_session = deepcopy(session)
    scene = _scene("closed-24", 34, "BUY")
    scene["closed_candle_transition_reason"] = (
        "VISUAL_CLOSED_CANDLE_GAP_REACQUIRED"
    )
    scene["closed_candle_identity_state"].update(
        {
            "transition_count": 24,
            "reacquisition": {
                "status": "CONFIRMED",
                "reason": "FORMER_LIVE_BAR_FOUND_IN_CONTIGUOUS_CLOSED_HISTORY",
                "confirmed_closed_count": 24,
            },
            "confirmed_event_batch": [
                {
                    "closed_candle_key": f"closed-{step}",
                    "closed_candle_sequence": 10 + step,
                    "observation": {
                        "track_id": str(10 + step),
                        "side": "BUY" if step % 2 else "SELL",
                        "price_proxy": 0.50 + step * 0.01,
                    },
                    "confirmation_reason": (
                        "VISUAL_CLOSED_CANDLE_GAP_REACQUIRED"
                    ),
                    "reacquired": True,
                }
                for step in range(1, 25)
            ],
        }
    )
    cast(dict[str, Any], recovered_session["forecast_snapshot_v3"])[
        "scene_forecast_contribution"
    ] = scene
    cast(dict[str, Any], recovered_session["latest_signal"])[
        "scene_forecast_contribution"
    ] = scene

    completed = advance_tracking_episode_v1(
        started,
        recovered_session,
        now_iso="2026-07-18T02:00:00+00:00",
    )

    assert completed["state"] == "COMPLETED"
    assert completed["event_cursor"] == TRACKING_EPISODE_HORIZON
    assert len(completed["events"]) == TRACKING_EPISODE_HORIZON
    assert [row["actual_block"]["track_id"] for row in completed["events"]] == [
        str(sequence) for sequence in range(11, 23)
    ]


def test_authoritative_sequence_gap_records_unknown_unscored_events_publicly() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-unknown-gap",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    jumped = _next_closed_event(session, step=3)

    advanced = advance_tracking_episode_v1(
        started,
        jumped,
        now_iso="2026-07-18T00:15:00+00:00",
    )
    public = project_public_tracking_episode_v1(
        {"tracking_episode": advanced}
    )

    assert advanced["event_cursor"] == 3
    assert [row["observation_kind"] for row in advanced["events"]] == [
        "UNKNOWN_GAP",
        "UNKNOWN_GAP",
        "LIVE_CLOSE",
    ]
    assert advanced["events"][0].get("direction_agreement") is None
    assert advanced["events"][1].get("direction_agreement") is None
    public_events = cast(list[dict[str, Any]], public["events"])
    assert public_events[0]["agreement"] is None
    assert public_events[0]["result_available"] is False
    assert "unscored" in str(public_events[0]["summary"])


def test_public_episode_redacts_continuity_telemetry_and_never_infers_agreement() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-public-redaction",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    advanced = advance_tracking_episode_v1(
        started,
        _next_closed_event(session, step=1),
        now_iso="2026-07-18T00:05:00+00:00",
    )
    public_source = deepcopy(advanced)
    cast(dict[str, Any], public_source["events"][0]).pop(
        "direction_agreement",
        None,
    )

    public = project_public_tracking_episode_v1(
        {"tracking_episode": public_source}
    )
    public_event = cast(list[dict[str, Any]], public["events"])[0]
    observation = cast(dict[str, Any], public["observation"])

    assert public_event["agreement"] is None
    assert public_event["result_available"] is True
    assert "observation_kind" not in public_event
    assert "frame_id" not in public_event
    assert set(observation) == {
        "schema_version",
        "status",
        "message",
        "unresolved_gap",
    }

    bounded = mobile_app._bounded_operator_projection_context(  # pyright: ignore[reportPrivateUsage]
        {"tracking_episode": public_source}
    )
    bounded_episode = cast(dict[str, Any], bounded["tracking_episode"])
    assert bounded_episode["observation_state"] == {
        "status": "LIVE",
        "unresolved_gap": False,
    }
    bounded_event = cast(list[dict[str, Any]], bounded_episode["events"])[0]
    assert "observation_kind" not in bounded_event
    assert "frame_id" not in bounded_event
    assert "after_reference" not in bounded_event
    assert bounded_event["path_fit_by_id"]["PATH_A"] == {
        "status": "MEASURED",
        "direction_agreement": True,
    }
    assert "fit" not in bounded_event["path_fit_by_id"]["PATH_A"]
    assert "error" not in bounded_event["path_fit_by_id"]["PATH_A"]
    assert bounded_event["entry_location_progress"] == {"status": "UNKNOWN"}
    assert bounded_episode["path_comparison"]["transform_contract"] == {
        "status": "LOCKED"
    }

    workspace = build_operator_workspace_v1(
        bounded,
        now_epoch=1_780_000_011.0,
    )
    workspace_episode = cast(
        dict[str, Any],
        cast(dict[str, Any], workspace["tracking"])["episode"],
    )
    workspace_comparison = cast(
        dict[str, Any],
        workspace_episode["path_comparison"],
    )
    workspace_event = cast(list[dict[str, Any]], workspace_episode["events"])[0]
    assert len(workspace_comparison["paths"]) == 2
    assert all(len(path["points"]) == 13 for path in workspace_comparison["paths"])
    assert all(len(path["steps"]) == 12 for path in workspace_comparison["paths"])
    assert workspace_comparison["verdict"] == "WAITING"
    assert workspace_event["observed_close_level"] == 0.49
    serialized_bounded = json.dumps(bounded).lower()
    assert "surface_transform_identity" not in serialized_bounded
    assert "chart_transform_identity" not in serialized_bounded
    assert '"distance"' not in serialized_bounded


def test_active_episode_exposes_reacquiring_state_without_advancing() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-reacquiring",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    degraded = deepcopy(session)
    scene = cast(
        dict[str, Any],
        cast(dict[str, Any], degraded["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    scene["closed_candle_transition_reason"] = "AMBIGUOUS_SCREENSHOT_REUSES_EVENT"
    scene["closed_candle_match_scores"] = {
        "coverage_degradation_observed": True,
    }
    cast(dict[str, Any], degraded["latest_signal"])[
        "scene_forecast_contribution"
    ] = scene
    degraded["frame_index"] = 44
    degraded["model_vote_frame_id"] = 44
    degraded["last_capture_at"] = "2026-07-18T00:01:00+00:00"

    unresolved = advance_tracking_episode_v1(
        started,
        degraded,
        now_iso="2026-07-18T00:01:00+00:00",
    )
    public = project_public_tracking_episode_v1(
        {"tracking_episode": unresolved}
    )

    assert unresolved["event_cursor"] == 0
    assert unresolved["state"] == "ACTIVE"
    assert unresolved["observation_state"]["status"] == "REACQUIRING"
    assert unresolved["observation_state"]["unresolved_gap"] is True
    observation = cast(dict[str, Any], public["observation"])
    assert observation["status"] == "REACQUIRING"
    assert observation["unresolved_gap"] is True


def test_tracking_episode_rejects_stale_closed_sequence_and_invalidates_identity_change() -> None:
    session = _ready_session()
    started = start_tracking_episode_v1(
        default_tracking_episode_v1(session_id="episode-test"),
        session,
        episode_id="episode-identity",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    stale = _next_closed_event(session, step=1)
    stale_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], stale["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    stale_scene["closed_candle_key"] = "different-key-same-sequence"
    stale_scene["closed_candle_sequence"] = 10
    stale_scene["closed_candle_identity_state"]["event_key"] = (
        "different-key-same-sequence"
    )
    stale_scene["closed_candle_identity_state"]["event_sequence"] = 10

    unchanged = advance_tracking_episode_v1(
        started,
        stale,
        now_iso="2026-07-18T00:01:00+00:00",
    )
    changed = _next_closed_event(session, step=1)
    cast(dict[str, Any], changed["forecast_snapshot_v3"])["pair"] = "GBPUSD_OTC"
    cast(
        dict[str, Any],
        cast(dict[str, Any], changed["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )["pair"] = "GBPUSD_OTC"
    invalidated = advance_tracking_episode_v1(
        unchanged,
        changed,
        now_iso="2026-07-18T00:05:00+00:00",
    )

    assert unchanged["events"] == started["events"]
    assert unchanged["event_cursor"] == started["event_cursor"]
    assert unchanged["revision"] == started["revision"]
    assert unchanged["baseline_forecasts"] == started["baseline_forecasts"]
    assert invalidated["state"] == "INVALIDATED"
    assert invalidated["terminal_reason"] == "PAIR_OR_TIMEFRAME_CHANGED"
    assert invalidated["baseline_forecasts"] == started["baseline_forecasts"]
    assert invalidated["committed_plan"] == started["committed_plan"]
    assert invalidated["permission"]["active"] is False


def test_tracking_episode_completes_exactly_twelve_events_and_stop_preserves_data() -> None:
    session = _ready_session()
    episode = start_tracking_episode_v1(
        default_tracking_episode_v1(session_id="episode-test"),
        session,
        episode_id="episode-twelve",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    for step in range(1, TRACKING_EPISODE_HORIZON + 1):
        episode = advance_tracking_episode_v1(
            episode,
            _next_closed_event(session, step=step),
            now_iso=f"2026-07-18T{step:02d}:00:00+00:00",
        )

    assert episode["state"] == "COMPLETED"
    assert episode["event_cursor"] == TRACKING_EPISODE_HORIZON
    assert len(episode["events"]) == TRACKING_EPISODE_HORIZON
    assert episode["permission"]["active"] is False

    stopped_again = stop_tracking_episode_v1(
        episode,
        session_id="episode-test",
        now_iso="2026-07-18T13:00:00+00:00",
    )
    assert stopped_again == episode


def test_tracking_episode_reset_is_terminal_only_and_returns_clean_idle() -> None:
    active = start_tracking_episode_v1(
        default_tracking_episode_v1(session_id="episode-test"),
        _ready_session(),
        episode_id="episode-reset",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    with pytest.raises(TrackingEpisodeStateError, match="Stop and save"):
        reset_tracking_episode_v1(active, session_id="episode-test")

    stopped = stop_tracking_episode_v1(
        active,
        session_id="episode-test",
        now_iso="2026-07-18T00:01:00+00:00",
    )
    reset = reset_tracking_episode_v1(stopped, session_id="episode-test")

    assert reset == default_tracking_episode_v1(session_id="episode-test")
    assert reset_tracking_episode_v1(reset, session_id="episode-test") == reset


def test_tracking_episode_history_is_newest_first_bounded_and_deduplicated() -> None:
    session = _ready_session()
    first = start_tracking_episode_v1(
        {},
        session,
        episode_id="episode-one",
        now_iso="2026-07-18T00:00:00+00:00",
    )
    for step in (1, 2):
        first = advance_tracking_episode_v1(
            first,
            _next_closed_event(session, step=step),
            now_iso=f"2026-07-18T00:0{step}:00+00:00",
        )
    first = stop_tracking_episode_v1(
        first,
        session_id="episode-test",
        now_iso="2026-07-18T00:03:00+00:00",
    )
    second = start_tracking_episode_v1(
        first,
        session,
        episode_id="episode-two",
        now_iso="2026-07-18T00:04:00+00:00",
    )
    second = advance_tracking_episode_v1(
        second,
        _next_closed_event(session, step=1),
        now_iso="2026-07-18T00:05:00+00:00",
    )
    second = stop_tracking_episode_v1(
        second,
        session_id="episode-test",
        now_iso="2026-07-18T00:06:00+00:00",
    )

    history = update_tracking_episode_history_v1([], first)
    history = update_tracking_episode_history_v1(history, second)
    history = update_tracking_episode_history_v1(history, first)

    assert [row["episode_id"] for row in history] == ["episode-one", "episode-two"]
    assert history[0]["state"] == "STOPPED"
    assert [event["event_id"] for event in history[0]["events"]] == [
        "episode-one:E1",
        "episode-one:E2",
    ]
    assert history[0]["events"][0] == {
        "event_id": "episode-one:E1",
        "step": 1,
        "observed_at": "2026-07-18T00:01:00+00:00",
        "predicted_side": "BUY",
        "actual_side": "BUY",
        "direction_agreement": True,
        "frame_id": 11,
    }
    assert len(history[1]["events"]) == 1


def test_recent_study_compaction_keeps_newest_rows_and_lineage() -> None:
    compact = live_state_v3._compact_recent_studies(  # pyright: ignore[reportPrivateUsage]
        [
            {"summary": "newest", "frame_id": 30, "captured_at": "new"},
            {"summary": "middle", "frame_id": 20, "captured_at": "middle"},
            {"summary": "oldest", "frame_id": 10, "captured_at": "old"},
        ],
        limit=2,
    )

    assert [row["summary"] for row in compact] == ["newest", "middle"]
    assert compact[0]["frame_id"] == 30
    assert compact[0]["captured_at"] == "new"


class _NoCaptureBackend:
    def list_windows(self, query: str | None = None) -> list[dict[str, Any]]:
        _ = query
        return []


class _NoTrackingAdapter:
    pass


class _NoFocusSelector:
    def is_supported(self) -> bool:
        return False


def _persist_ready_service_session(
    tracker: ContinuousWindowTrackerService,
) -> None:
    tracker.create_session(session_id="episode-test")
    payload = tracker.load_session_payload("episode-test")
    payload.update(_ready_session())
    payload["tracking_episode"] = default_tracking_episode_v1(
        session_id="episode-test"
    )
    payload["tracking_episode_history"] = []
    payload["recent_studies"] = [{"summary": "preserve-me", "frame_id": 9}]
    tracker.save_session(payload)


def test_service_episode_controls_preserve_worker_state_history_and_prior_episode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "window_tracker",
        capture_backend=_NoCaptureBackend(),
        tracking_adapter=_NoTrackingAdapter(),
        focus_selector_backend=_NoFocusSelector(),  # type: ignore[arg-type]
    )
    try:
        _persist_ready_service_session(tracker)
        warm_tracking_adapter = tracker.tracking_adapter
        warm_model_council = tracker._model_council_for_session(  # pyright: ignore[reportPrivateUsage]
            "episode-test"
        )

        def fail_ensure_worker(
            _session_id: str,
            *,
            capture_now: bool = False,
        ) -> None:
            _ = capture_now
            pytest.fail("episode Start must not start a worker")

        def fail_stop_worker(_session_id: str) -> None:
            pytest.fail("episode Stop must not stop a worker")

        monkeypatch.setattr(
            tracker,
            "_ensure_worker",
            fail_ensure_worker,
        )
        monkeypatch.setattr(
            tracker,
            "_stop_worker",
            fail_stop_worker,
        )

        first = tracker.start_tracking_episode("episode-test")
        duplicate = tracker.start_tracking_episode("episode-test")
        stopped = tracker.stop_tracking_episode("episode-test")
        duplicate_stop = tracker.stop_tracking_episode("episode-test")
        reset = tracker.reset_tracking_episode("episode-test")
        duplicate_reset = tracker.reset_tracking_episode("episode-test")
        reset_state_path = tracker.session_dir("episode-test") / "tracking_episode_state.json"
        reset_state = json.loads(reset_state_path.read_text(encoding="utf-8"))
        reset_ledger = (
            tracker.session_dir("episode-test") / "tracking_episode_events.jsonl"
        ).read_text(encoding="utf-8")
        second = tracker.start_tracking_episode("episode-test")
        persisted = tracker.load_session_payload("episode-test")

        assert duplicate["episode_id"] == first["episode_id"]
        assert duplicate["revision"] == first["revision"]
        assert duplicate_stop == stopped
        assert reset["state"] == "IDLE"
        assert reset["episode_id"] == ""
        assert duplicate_reset == reset
        assert reset_state["state"] == "IDLE"
        assert reset_state["episode_id"] == ""
        assert '"source": "reset_tracking"' in reset_ledger
        assert second["episode_id"] != first["episode_id"]
        assert tracker.tracking_adapter is warm_tracking_adapter
        assert (
            tracker._model_council_for_session(  # pyright: ignore[reportPrivateUsage]
                "episode-test"
            )
            is warm_model_council
        )
        assert stopped["runtime_policy"] == {
            "capture_worker": "ALWAYS_WARM",
            "models": "ALWAYS_WARM",
            "stop_scope": "EPISODE_ONLY",
        }
        assert persisted["tracking_enabled"] is True
        assert persisted["recent_studies"][0]["summary"] == "preserve-me"
        history = cast(list[dict[str, Any]], persisted["tracking_episode_history"])
        assert history[0]["episode_id"] == first["episode_id"]
        assert history[0]["state"] == "STOPPED"

        episode_dir = tracker.session_dir("episode-test") / "tracking_episodes"
        assert (episode_dir / f"{first['episode_id']}.json").is_file()
        assert (tracker.session_dir("episode-test") / "tracking_episode_events.jsonl").is_file()
        state_path = tracker.session_dir("episode-test") / "tracking_episode_state.json"
        assert json.loads(state_path.read_text(encoding="utf-8"))["episode_id"] == second["episode_id"]
    finally:
        tracker.shutdown()


def test_terminal_episode_archive_survives_a_fresh_runtime_root_without_authority(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "persistent_episode_archive"
    first_tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "runtime_one" / "window_tracker",
        tracking_episode_archive_root=archive_root,
        capture_backend=_NoCaptureBackend(),
        tracking_adapter=_NoTrackingAdapter(),
        focus_selector_backend=_NoFocusSelector(),  # type: ignore[arg-type]
    )
    try:
        _persist_ready_service_session(first_tracker)
        started = first_tracker.start_tracking_episode("episode-test")
        payload = first_tracker.load_session_payload("episode-test")
        payload["tracking_episode"] = advance_tracking_episode_v1(
            started,
            _next_closed_event(_ready_session(), step=1),
            now_iso="2026-07-18T00:05:00+00:00",
        )
        first_tracker.save_session(payload)
        stopped = first_tracker.stop_tracking_episode("episode-test")
        assert stopped["episode_id"] == started["episode_id"]
    finally:
        first_tracker.shutdown()

    # A different empty live root models the launcher's clean-runtime step.
    restarted_tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "runtime_two" / "window_tracker",
        tracking_episode_archive_root=archive_root,
        capture_backend=_NoCaptureBackend(),
        tracking_adapter=_NoTrackingAdapter(),
        focus_selector_backend=_NoFocusSelector(),  # type: ignore[arg-type]
    )
    try:
        restored = restarted_tracker.create_session(session_id="episode-test")
        current = cast(dict[str, Any], restored["tracking_episode"])
        history = cast(list[dict[str, Any]], restored["tracking_episode_history"])

        assert current["state"] == "IDLE"
        assert current["episode_id"] == ""
        assert current["permission"]["active"] is False
        assert history[0]["episode_id"] == started["episode_id"]
        assert history[0]["state"] == "STOPPED"
        assert history[0]["events"] == [
            {
                "event_id": f"{started['episode_id']}:E1",
                "step": 1,
                "observed_at": "2026-07-18T00:05:00+00:00",
                "predicted_side": "BUY",
                "actual_side": "BUY",
                "direction_agreement": True,
                "frame_id": 11,
            }
        ]
        durable_record = (
            archive_root
            / "sessions"
            / "episode-test"
            / "episodes"
            / f"{started['episode_id']}.json"
        )
        durable_payload = json.loads(durable_record.read_text(encoding="utf-8"))
        assert durable_payload["state"] == "STOPPED"
        assert len(durable_payload["baseline_forecasts"]["lstm"]["forecast_path"]) == 12
        assert (
            archive_root / "sessions" / "episode-test" / "events.jsonl"
        ).is_file()
    finally:
        restarted_tracker.shutdown()


def test_tracking_episode_api_routes_are_separate_from_worker_start_stop(
    tmp_path: Path,
) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "window_tracker",
        capture_backend=_NoCaptureBackend(),
        tracking_adapter=_NoTrackingAdapter(),
        focus_selector_backend=_NoFocusSelector(),  # type: ignore[arg-type]
    )
    try:
        _persist_ready_service_session(tracker)
        with TestClient(create_app(window_tracker_service=tracker)) as client:
            readiness = client.get(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/readiness"
            )
            started = client.post(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/start"
            )
            active_reset = client.post(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/reset"
            )
            current = client.get(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/current"
            )
            stopped = client.post(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/stop",
                json={"reason": "operator_stop"},
            )
            reset = client.post(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/reset"
            )

        assert readiness.status_code == 200
        assert readiness.json()["ready"] is True
        assert started.status_code == 200
        assert active_reset.status_code == 409
        assert active_reset.json()["detail"]["code"] == "TRACKING_EPISODE_RESET_NOT_ALLOWED"
        assert active_reset.json()["detail"]["state"] == "ACTIVE"
        assert current.json()["episode_id"] == started.json()["episode_id"]
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "STOPPED"
        assert stopped.json()["terminal_reason"] == "STOPPED"
        assert reset.status_code == 200
        assert reset.json()["state"] == "IDLE"
        assert reset.json()["episode_id"] == ""
        for response in (started, current, stopped, reset):
            public_episode = response.json()
            assert public_episode["schema_version"] == "PG_TRACKING_EPISODE_PUBLIC_V1"
            assert "baseline_forecasts" not in public_episode
            assert "committed_plan" not in public_episode
            assert "candidate_revision" not in public_episode
            assert "runtime_policy" not in public_episode
    finally:
        tracker.shutdown()
