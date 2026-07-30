from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from Backend.tools.certification_common_v3 import HttpResult
from Backend.tools.certify_cpu_stream_runtime_v3 import (
    RuntimeCertificationConfig,
    certify_runtime,
)


class _FakeClock:
    def __init__(self, epoch: float = 1_800_000_000.0) -> None:
        self.epoch = epoch

    def __call__(self) -> float:
        return self.epoch

    def sleep(self, duration: float) -> None:
        self.epoch += duration


def test_cpu_only_certification_defaults_allow_bounded_commit_jitter() -> None:
    config = RuntimeCertificationConfig()

    assert config.min_fps_ratio == 0.30
    assert config.max_stream_age_sec == 8.0


def _ok(payload: Mapping[str, object]) -> HttpResult:
    return HttpResult(ok=True, status=200, latency_ms=4.0, payload=dict(payload))


def _stream_payload(clock: _FakeClock, call_index: int) -> dict[str, object]:
    frame_seq = call_index * 2
    accepted = call_index
    dropped = max(0, call_index - 3)
    lineage = {
        "stream_id": "pgcpu-live-test",
        "stream_generation": 1,
        "frame_seq": frame_seq,
        "captured_epoch": clock(),
        "broker_click_authority": False,
    }
    return {
        "session_id": "stream-cert-test",
        "updated_at": datetime_text(clock),
        "last_capture_epoch": clock(),
        "cpu_stream_v3": {
            "requested": True,
            "enabled": True,
            "available": True,
            "status": "active",
            "mode": "event_driven_cpu_stream",
            "target_fps": 4.0,
            "actual_fps": 3.95,
            "started_epoch": clock() - 30.0,
            "status_updated_epoch": clock(),
            "last_capture_epoch": clock(),
            "last_event_epoch": clock(),
            "observed_frames": frame_seq,
            "accepted_events": accepted,
            "dropped_keyframes": dropped,
            "capture_errors": 0,
            "stale_generation_drops": 0,
            "keyframe_slot_capacity": 1,
            "pending_keyframe": call_index % 2 == 0,
            "full_model_policy": "ACCEPTED_EVENT_OR_HEARTBEAT_ONLY",
            "broker_click_authority": False,
            "last_keyframe_lineage": lineage,
            "last_observation_lineage": lineage,
            "observer": {
                "status": "healthy",
                "cpu_only": True,
                "stream_id": "pgcpu-live-test",
                "stream_generation": 1,
                "frame_seq": frame_seq,
                "last_captured_epoch": clock(),
                "rings": {
                    "full_frames": {
                        "size": 2,
                        "capacity": 2,
                        "dropped": max(0, frame_seq - 2),
                    },
                    "downsamples": {
                        "size": min(frame_seq, 48),
                        "capacity": 48,
                        "dropped": 0,
                    },
                },
                "memory": {
                    "current_estimated_pixel_bytes": 12_500_000,
                    "configured_upper_bound_pixel_bytes": 101_200_000,
                },
                "counters": {
                    "full_frame_ring_drops": max(0, frame_seq - 2),
                    "downsample_ring_drops": 0,
                    "latest_frame_wins_drops": dropped,
                },
            },
        },
    }


def datetime_text(clock: _FakeClock) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(clock(), tz=UTC).isoformat()


def test_live_certification_passes_bounded_monotonic_mocked_runtime() -> None:
    clock = _FakeClock()
    session_calls = 0

    def fetch(url: str, *, timeout: float) -> HttpResult:
        nonlocal session_calls
        assert timeout == 1.0
        if "/window-tracker/sessions/" in url:
            session_calls += 1
            return _ok(_stream_payload(clock, session_calls))
        if url.endswith("/v1/mobile/health"):
            return _ok({"status": "ok"})
        if "/operator/state/v1/" in url:
            return _ok(
                {
                    "three_questions": {
                        "schema_version": "PG_THREE_QUESTION_OPERATOR_BRIEF_V3",
                        "market_origin_history": {"answer": "History rose."},
                        "studied_direction_current": {"answer": "SELL reaction."},
                        "entry_now": {"action": "DO_NOT_ENTER"},
                    }
                }
            )
        raise AssertionError(f"unexpected URL {url}")

    report = certify_runtime(
        RuntimeCertificationConfig(
            base_url="http://127.0.0.1:8793",
            session_id="stream-cert-test",
            duration_sec=2.0,
            interval_sec=0.5,
            timeout_sec=1.0,
        ),
        fetch_json=fetch,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert report["verdict"] == "PASS"
    gates = cast(dict[str, dict[str, Any]], report["gates"])
    assert gates["observed_frame_advancement"]["status"] == "PASS"
    assert gates["observed_frame_advancement"]["evidence"]["measured_fps"] == 4.0
    assert gates["single_keyframe_slot"]["status"] == "PASS"
    assert gates["bounded_observer_resources"]["status"] == "PASS"
    assert gates["no_unbounded_backlog"]["status"] == "PASS"
    assert gates["exactly_three_operator_questions"]["status"] == "PASS"


def test_live_certification_uses_stream_sidecar_heartbeat_for_session_freshness() -> None:
    clock = _FakeClock()
    session_calls = 0

    def fetch(url: str, *, timeout: float) -> HttpResult:
        nonlocal session_calls
        assert timeout == 1.0
        if "/window-tracker/sessions/" in url:
            session_calls += 1
            payload = _stream_payload(clock, session_calls)
            payload["updated_at"] = datetime_text(_FakeClock(clock() - 600.0))
            payload["last_capture_epoch"] = clock() - 600.0
            return _ok(payload)
        if url.endswith("/v1/mobile/health"):
            return _ok({"status": "ok"})
        if "/operator/state/v1/" in url:
            return _ok(
                {
                    "three_questions": {
                        "schema_version": "PG_THREE_QUESTION_OPERATOR_BRIEF_V3",
                        "market_origin_history": {"answer": "History rose."},
                        "studied_direction_current": {"answer": "SELL reaction."},
                        "entry_now": {"action": "DO_NOT_ENTER"},
                    }
                }
            )
        raise AssertionError(f"unexpected URL {url}")

    report = certify_runtime(
        RuntimeCertificationConfig(
            base_url="http://127.0.0.1:8793",
            session_id="stream-cert-test",
            duration_sec=2.0,
            interval_sec=0.5,
            timeout_sec=1.0,
        ),
        fetch_json=fetch,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert report["verdict"] == "PASS"
    gates = cast(dict[str, dict[str, Any]], report["gates"])
    assert gates["session_api_freshness"]["status"] == "PASS"


def test_live_certification_fails_unsafe_or_unbounded_mocked_contract() -> None:
    clock = _FakeClock()
    session_calls = 0

    def fetch(url: str, *, timeout: float) -> HttpResult:
        nonlocal session_calls
        del timeout
        if "/window-tracker/sessions/" in url:
            session_calls += 1
            payload = _stream_payload(clock, session_calls)
            stream = cast(dict[str, Any], payload["cpu_stream_v3"])
            stream["broker_click_authority"] = True
            stream["keyframe_slot_capacity"] = 2
            stream["pending_keyframe"] = 2
            observer = cast(dict[str, Any], stream["observer"])
            rings = cast(dict[str, Any], observer["rings"])
            cast(dict[str, Any], rings["full_frames"])["capacity"] = 99
            memory = cast(dict[str, Any], observer["memory"])
            memory["current_estimated_pixel_bytes"] = 500_000_000
            memory["configured_upper_bound_pixel_bytes"] = 500_000_000
            if session_calls >= 3:
                stream["accepted_events"] = 0
                stream["dropped_keyframes"] = 0
            return _ok(payload)
        if url.endswith("/v1/mobile/health"):
            return _ok({"status": "ok"})
        if "/operator/state/v1/" in url:
            return _ok(
                {
                    "three_questions": {
                        "schema_version": "PG_THREE_QUESTION_OPERATOR_BRIEF_V3",
                        "market_origin_history": {},
                        "studied_direction_current": {},
                        "entry_now": {},
                        "extra_question": {},
                    }
                }
            )
        raise AssertionError(f"unexpected URL {url}")

    report = certify_runtime(
        RuntimeCertificationConfig(
            session_id="stream-cert-test",
            duration_sec=2.0,
            interval_sec=0.5,
            timeout_sec=1.0,
        ),
        fetch_json=fetch,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert report["verdict"] == "FAIL"
    gates = cast(dict[str, dict[str, Any]], report["gates"])
    assert gates["monotonic_stream_counters"]["status"] == "FAIL"
    assert gates["single_keyframe_slot"]["status"] == "FAIL"
    assert gates["bounded_observer_resources"]["status"] == "FAIL"
    assert gates["no_broker_click_authority"]["status"] == "FAIL"
    assert gates["exactly_three_operator_questions"]["status"] == "FAIL"
    assert gates["no_unbounded_backlog"]["status"] == "FAIL"


def test_operator_question_gate_skips_when_endpoint_is_unavailable() -> None:
    clock = _FakeClock()
    session_calls = 0

    def fetch(url: str, *, timeout: float) -> HttpResult:
        nonlocal session_calls
        del timeout
        if "/window-tracker/sessions/" in url:
            session_calls += 1
            return _ok(_stream_payload(clock, session_calls))
        if url.endswith("/v1/mobile/health"):
            return _ok({"status": "ok"})
        return HttpResult(ok=False, status=404, latency_ms=2.0, error="not found")

    report = certify_runtime(
        RuntimeCertificationConfig(
            session_id="stream-cert-test",
            duration_sec=2.0,
            interval_sec=0.5,
            timeout_sec=1.0,
        ),
        fetch_json=fetch,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert report["verdict"] == "PASS"
    gates = cast(dict[str, dict[str, Any]], report["gates"])
    assert gates["exactly_three_operator_questions"]["status"] == "SKIP"
    assert report["warnings"]
