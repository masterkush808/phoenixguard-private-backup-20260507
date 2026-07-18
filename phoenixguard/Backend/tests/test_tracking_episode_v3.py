from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
import pytest

from phoenixguard.mobile_api import live_state_v3
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
from phoenixguard.tracking.tracking_episode_v3 import (
    TRACKING_EPISODE_HORIZON,
    TrackingEpisodeReadinessError,
    advance_tracking_episode_v1,
    default_tracking_episode_v1,
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
    return {
        "pair": "EURUSD_OTC",
        "timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
        "closed_candle_key": key,
        "closed_candle_sequence": sequence,
        "closed_candle_identity_state": {
            "event_key": key,
            "event_sequence": sequence,
            "latest_closed": {"track_id": str(sequence), "side": side},
        },
        "path_side": side,
        "forecast_candles": candles,
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
                }
            ],
        },
        "signal_thesis_v3": {
            "thesis_id": "thesis-1",
            "side": side,
            "state": "ACTIVE",
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
        }
    ]
    updated["frame_index"] = sequence
    updated["display_frame_id"] = sequence
    updated["model_vote_frame_id"] = sequence
    updated["source_capture_id"] = f"capture-{sequence}"
    updated["last_study_surface_signature"] = f"surface-{sequence}"
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
    assert same_forming_candle == started
    assert len(started["baseline_forecasts"]["lstm"]["forecast_path"]) == 12
    assert first_event["event_cursor"] == 1
    assert first_event["events"][0]["event_id"] == "episode-fixed:E1"
    assert first_event["events"][0]["direction_agreement"] is True
    assert first_event["baseline_forecasts"] == started["baseline_forecasts"]
    assert first_event["committed_plan"] == started["committed_plan"]


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

    assert unchanged == started
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
        second = tracker.start_tracking_episode("episode-test")
        persisted = tracker.load_session_payload("episode-test")

        assert duplicate["episode_id"] == first["episode_id"]
        assert duplicate["revision"] == first["revision"]
        assert duplicate_stop == stopped
        assert second["episode_id"] != first["episode_id"]
        assert tracker.tracking_adapter is warm_tracking_adapter
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
            current = client.get(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/current"
            )
            stopped = client.post(
                "/v1/mobile/window-tracker/sessions/episode-test/tracking-episodes/stop",
                json={"reason": "operator_stop"},
            )

        assert readiness.status_code == 200
        assert readiness.json()["ready"] is True
        assert started.status_code == 200
        assert current.json()["episode_id"] == started.json()["episode_id"]
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "STOPPED"
        assert stopped.json()["terminal_reason"] == "STOPPED"
        for response in (started, current, stopped):
            public_episode = response.json()
            assert public_episode["schema_version"] == "PG_TRACKING_EPISODE_PUBLIC_V1"
            assert "baseline_forecasts" not in public_episode
            assert "committed_plan" not in public_episode
            assert "candidate_revision" not in public_episode
            assert "runtime_policy" not in public_episode
    finally:
        tracker.shutdown()
