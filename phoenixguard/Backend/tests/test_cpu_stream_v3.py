from __future__ import annotations

import re

import pytest
from PIL import Image, ImageDraw

from phoenixguard.vision.cpu_stream_v3 import (
    CPU_STREAM_DECISION_SCHEMA_VERSION,
    CPU_STREAM_HEALTH_SCHEMA_VERSION,
    CPU_STREAM_TEMPORAL_EVIDENCE_SCHEMA_VERSION,
    CPUStreamConfig,
    CPUStreamObserver,
)


IDENTITY = {"symbol": "EUR/USD OTC", "timeframe": "M5", "source": "pocket"}


def _solid(value: int, size: tuple[int, int] = (160, 90)) -> Image.Image:
    return Image.new("RGB", size, (value, value, value))


def _changed_half(value: int = 255, size: tuple[int, int] = (160, 90)) -> Image.Image:
    image = _solid(0, size)
    draw = ImageDraw.Draw(image)
    draw.rectangle((size[0] // 2, 0, size[0] - 1, size[1] - 1), fill=(value, value, value))
    return image


def test_config_defaults_are_bounded_and_stable() -> None:
    config = CPUStreamConfig()

    assert config.full_frame_capacity == 2
    assert config.downsample_ring_capacity == 48
    assert config.downsample_size == (128, 72)
    assert config.max_frame_pixels == 16_777_216
    assert config.keyframe_min_interval_sec == 0.25
    assert config.heartbeat_interval_sec == 5.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"full_frame_capacity": 0}, "full_frame_capacity"),
        ({"downsample_ring_capacity": 1}, "downsample_ring_capacity"),
        ({"downsample_size": (7, 72)}, "downsample_size"),
        ({"max_frame_pixels": 100}, "max_frame_pixels"),
        ({"rest_motion_score_threshold": 0.1, "material_motion_score_threshold": 0.05}, "rest_motion_score_threshold"),
        ({"heartbeat_interval_sec": 0.1, "keyframe_min_interval_sec": 0.25}, "heartbeat_interval_sec"),
    ],
)
def test_config_rejects_unbounded_or_incoherent_values(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CPUStreamConfig(**kwargs)  # type: ignore[arg-type]


def test_first_frame_is_lineage_bound_keyframe_with_stable_hashes() -> None:
    first = CPUStreamObserver(stream_id="deterministic-stream")
    second = CPUStreamObserver(stream_id="other-stream")
    image = _solid(33)

    decision = first.push(image, captured_epoch=100.0, identity=IDENTITY)
    same_content = second.push(image.copy(), captured_epoch=100.0, identity=dict(reversed(list(IDENTITY.items()))))

    assert decision.accepted_for_study is True
    assert decision.reason == "stream_start"
    assert decision.frame_seq == 1
    assert decision.stream_id == "deterministic-stream"
    assert decision.stream_generation == 1
    assert decision.input_frame_hash == same_content.input_frame_hash
    assert re.fullmatch(r"[0-9a-f]{64}", decision.input_frame_hash)
    evidence = decision.temporal_evidence
    assert evidence["schema_version"] == CPU_STREAM_TEMPORAL_EVIDENCE_SCHEMA_VERSION
    assert evidence["input_frame_hash_algorithm"] == "sha256"
    assert re.fullmatch(r"[0-9a-f]{64}", str(evidence["input_frame_blake2b"]))
    assert evidence["identity_hash"] == same_content.temporal_evidence["identity_hash"]
    assert decision.as_dict()["schema_version"] == CPU_STREAM_DECISION_SCHEMA_VERSION


def test_duplicate_is_suppressed_until_accepted_heartbeat() -> None:
    observer = CPUStreamObserver(stream_id="heartbeat")
    image = _solid(20)

    observer.push(image, captured_epoch=10.0, identity=IDENTITY)
    duplicate = observer.push(image, captured_epoch=10.5, identity=IDENTITY)
    heartbeat = observer.push(image, captured_epoch=15.0, identity=IDENTITY)

    assert duplicate.accepted_for_study is False
    assert duplicate.reason == "duplicate"
    assert duplicate.temporal_evidence["state"] == "duplicate"
    assert heartbeat.accepted_for_study is True
    assert heartbeat.reason == "heartbeat"
    snapshot = observer.snapshot()
    counters = snapshot["counters"]
    assert isinstance(counters, dict)
    assert counters["duplicate_frames"] == 2
    assert counters["heartbeat_keyframes"] == 1


def test_heartbeat_is_suppressed_while_a_heavy_study_is_in_flight() -> None:
    observer = CPUStreamObserver(stream_id="busy-heartbeat")
    image = _solid(20)

    first = observer.push(image, captured_epoch=10.0, identity=IDENTITY)
    suppressed = observer.push(
        image,
        captured_epoch=16.0,
        identity=IDENTITY,
        allow_heartbeat=False,
    )
    admitted = observer.push(
        image,
        captured_epoch=16.1,
        identity=IDENTITY,
        allow_heartbeat=True,
    )

    assert first.accepted_for_study is True
    assert suppressed.accepted_for_study is False
    assert suppressed.reason == "duplicate"
    assert admitted.accepted_for_study is True
    assert admitted.reason == "heartbeat"
    assert observer.snapshot()["last_keyframe_seq"] == admitted.frame_seq


def test_material_frames_are_observed_but_coalesced_while_heavy_study_is_busy() -> None:
    observer = CPUStreamObserver(stream_id="busy-material")
    first = observer.push(_solid(0), captured_epoch=20.0, identity=IDENTITY)

    coalesced = observer.push(
        _changed_half(),
        captured_epoch=20.5,
        identity=IDENTITY,
        allow_heartbeat=False,
        allow_study_keyframe=False,
    )
    busy_snapshot = observer.snapshot()
    admitted = observer.push(
        _solid(0),
        captured_epoch=21.0,
        identity=IDENTITY,
        allow_study_keyframe=True,
    )

    assert coalesced.accepted_for_study is False
    assert coalesced.reason == "material_change_coalesced_during_study"
    assert busy_snapshot["last_keyframe_seq"] == first.frame_seq
    counters = busy_snapshot["counters"]
    assert isinstance(counters, dict)
    assert counters["coalesced_material_frames"] == 1
    assert admitted.accepted_for_study is True
    assert admitted.reason == "material_change"


def test_material_change_selects_keyframe_and_reports_motion_and_wick_proxies() -> None:
    observer = CPUStreamObserver(stream_id="material")
    observer.push(_solid(0), captured_epoch=20.0, identity=IDENTITY)

    decision = observer.push(_changed_half(), captured_epoch=20.5, identity=IDENTITY)

    assert decision.accepted_for_study is True
    assert decision.reason == "material_change"
    evidence = decision.temporal_evidence
    assert evidence["state"] == "material_change"
    change = evidence["change"]
    motion = evidence["motion"]
    wick_motion = evidence["wick_motion"]
    assert isinstance(change, dict)
    assert isinstance(motion, dict)
    assert isinstance(wick_motion, dict)
    assert change["changed_pixel_ratio"] > 0.45
    assert motion["motion_score"] >= observer.config.material_motion_score_threshold
    assert motion["bbox_normalized"]
    assert wick_motion["vertical_span_ratio"] > 0.95
    assert "upper_rejection_pressure" in wick_motion
    assert "lower_rejection_pressure" in wick_motion


def test_upper_region_change_produces_upper_temporal_extreme() -> None:
    observer = CPUStreamObserver(
        CPUStreamConfig(material_changed_pixel_ratio_threshold=0.05),
        stream_id="wick",
    )
    observer.push(_solid(0), captured_epoch=30.0, identity=IDENTITY)
    changed = _solid(0)
    draw = ImageDraw.Draw(changed)
    draw.rectangle((0, 0, changed.width - 1, changed.height // 4), fill="white")

    decision = observer.push(changed, captured_epoch=30.5, identity=IDENTITY)

    wick_motion = decision.temporal_evidence["wick_motion"]
    assert isinstance(wick_motion, dict)
    assert wick_motion["dominant_extreme"] == "UPPER"
    assert wick_motion["upper_activity"] > wick_motion["body_activity"]
    assert wick_motion["top_changed_y_normalized"] == 0.0


def test_low_amplitude_change_separates_rest_from_motion() -> None:
    observer = CPUStreamObserver(stream_id="states")
    observer.push(_solid(0), captured_epoch=40.0, identity=IDENTITY)

    rest = observer.push(_solid(2), captured_epoch=40.5, identity=IDENTITY)
    motion = observer.push(_solid(6), captured_epoch=41.0, identity=IDENTITY)

    assert rest.accepted_for_study is False
    assert rest.reason == "rest"
    assert rest.temporal_evidence["state"] == "rest"
    assert motion.accepted_for_study is False
    assert motion.reason == "motion_below_material_threshold"
    assert motion.temporal_evidence["state"] == "motion"
    health = observer.snapshot()
    counters = health["counters"]
    assert isinstance(counters, dict)
    assert counters["rest_frames"] == 1
    assert counters["motion_frames"] == 1


@pytest.mark.parametrize(
    "recovery_frame",
    (_solid(2), _solid(6), _changed_half()),
    ids=("rest", "motion", "material"),
)
def test_visible_recovery_force_admits_only_the_classified_fresh_frame(
    recovery_frame: Image.Image,
) -> None:
    observer = CPUStreamObserver(
        CPUStreamConfig(
            keyframe_min_interval_sec=10.0,
            heartbeat_interval_sec=30.0,
        ),
        stream_id="visible-recovery",
    )
    first = observer.push(_solid(0), captured_epoch=10.0, identity=IDENTITY)

    recovered = observer.push(
        recovery_frame,
        captured_epoch=10.5,
        identity=IDENTITY,
        allow_heartbeat=False,
        force_study_keyframe=True,
    )

    assert recovered.accepted_for_study is True
    assert recovered.reason == "visible_duplicate_recovery"
    assert recovered.stream_generation == first.stream_generation
    keyframe = recovered.temporal_evidence["keyframe"]
    assert isinstance(keyframe, dict)
    assert keyframe["forced_visible_recovery"] is True
    snapshot = observer.snapshot()
    assert snapshot["last_keyframe_seq"] == recovered.frame_seq
    assert snapshot["last_keyframe_hash"] == recovered.input_frame_hash
    counters = snapshot["counters"]
    assert isinstance(counters, dict)
    assert counters["visible_recovery_keyframes"] == 1
    assert counters["manual_resets"] == 0


def test_visible_recovery_force_cannot_admit_duplicate_rejected_or_busy_frame() -> None:
    observer = CPUStreamObserver(stream_id="recovery-fail-closed")
    image = _solid(20)
    observer.push(image, captured_epoch=20.0, identity=IDENTITY)

    duplicate = observer.push(
        image,
        captured_epoch=20.5,
        identity=IDENTITY,
        allow_heartbeat=False,
        force_study_keyframe=True,
    )
    busy = observer.push(
        _solid(22),
        captured_epoch=21.0,
        identity=IDENTITY,
        allow_heartbeat=False,
        allow_study_keyframe=False,
        force_study_keyframe=True,
    )
    rejected = observer.push(
        _changed_half(),
        captured_epoch=20.75,
        identity=IDENTITY,
        allow_heartbeat=False,
        force_study_keyframe=True,
    )

    assert duplicate.accepted_for_study is False
    assert duplicate.reason == "duplicate"
    assert busy.accepted_for_study is False
    assert busy.reason == "rest"
    assert rejected.accepted_for_study is False
    assert rejected.reason == "non_monotonic_capture_epoch"
    counters = observer.snapshot()["counters"]
    assert isinstance(counters, dict)
    assert counters["visible_recovery_keyframes"] == 0


@pytest.mark.parametrize(
    ("frame", "identity", "expected_reason"),
    (
        (_solid(20), {**IDENTITY, "symbol": "GBP/JPY OTC"}, "identity_reset"),
        (_solid(20, size=(180, 100)), IDENTITY, "geometry_reset"),
    ),
)
def test_visible_recovery_force_does_not_override_generation_reset_reason(
    frame: Image.Image,
    identity: dict[str, str],
    expected_reason: str,
) -> None:
    observer = CPUStreamObserver(stream_id="recovery-generation")
    first = observer.push(_solid(20), captured_epoch=30.0, identity=IDENTITY)

    reset = observer.push(
        frame,
        captured_epoch=30.5,
        identity=identity,
        allow_heartbeat=False,
        force_study_keyframe=True,
    )

    assert reset.accepted_for_study is True
    assert reset.reason == expected_reason
    assert reset.stream_generation == first.stream_generation + 1
    counters = observer.snapshot()["counters"]
    assert isinstance(counters, dict)
    assert counters["visible_recovery_keyframes"] == 0


def test_material_keyframes_are_throttled_by_minimum_interval() -> None:
    observer = CPUStreamObserver(
        CPUStreamConfig(keyframe_min_interval_sec=1.0, heartbeat_interval_sec=5.0),
        stream_id="throttle",
    )
    observer.push(_solid(0), captured_epoch=50.0, identity=IDENTITY)

    throttled = observer.push(_solid(255), captured_epoch=50.2, identity=IDENTITY)
    accepted = observer.push(_solid(0), captured_epoch=51.1, identity=IDENTITY)

    assert throttled.accepted_for_study is False
    assert throttled.reason == "material_change_throttled"
    assert accepted.accepted_for_study is True
    assert accepted.reason == "material_change"
    counters = observer.snapshot()["counters"]
    assert isinstance(counters, dict)
    assert counters["throttled_material_frames"] == 1


def test_identity_change_starts_new_generation_and_clears_temporal_rings() -> None:
    observer = CPUStreamObserver(stream_id="identity")
    first = observer.push(_solid(0), captured_epoch=60.0, identity=IDENTITY)
    observer.push(_solid(0), captured_epoch=60.5, identity=IDENTITY)

    switched = observer.push(
        _solid(0),
        captured_epoch=61.0,
        identity={**IDENTITY, "symbol": "GBP/JPY OTC"},
    )

    assert switched.accepted_for_study is True
    assert switched.reason == "identity_reset"
    assert switched.stream_generation == first.stream_generation + 1
    snapshot = observer.snapshot()
    rings = snapshot["rings"]
    counters = snapshot["counters"]
    assert isinstance(rings, dict)
    assert isinstance(counters, dict)
    assert rings["full_frames"]["size"] == 1
    assert rings["downsamples"]["size"] == 1
    assert counters["identity_resets"] == 1


def test_geometry_change_starts_new_generation() -> None:
    observer = CPUStreamObserver(stream_id="geometry")
    observer.push(_solid(0, (160, 90)), captured_epoch=70.0, identity=IDENTITY)

    changed = observer.push(_solid(0, (192, 108)), captured_epoch=70.5, identity=IDENTITY)

    assert changed.accepted_for_study is True
    assert changed.reason == "geometry_reset"
    assert changed.stream_generation == 2
    counters = observer.snapshot()["counters"]
    assert isinstance(counters, dict)
    assert counters["geometry_resets"] == 1


def test_bounded_rings_apply_latest_frame_wins_and_report_memory_bound() -> None:
    config = CPUStreamConfig(
        full_frame_capacity=1,
        downsample_ring_capacity=2,
        downsample_size=(32, 16),
        heartbeat_interval_sec=100.0,
    )
    observer = CPUStreamObserver(config, stream_id="bounded")
    latest = None
    for index in range(5):
        latest = observer.push(_solid(index), captured_epoch=80.0 + index, identity=IDENTITY)

    assert latest is not None
    snapshot = observer.snapshot()
    rings = snapshot["rings"]
    counters = snapshot["counters"]
    memory = snapshot["memory"]
    assert isinstance(rings, dict)
    assert isinstance(counters, dict)
    assert isinstance(memory, dict)
    assert rings["full_frames"] == {"size": 1, "capacity": 1, "dropped": 4}
    assert rings["downsamples"] == {"size": 2, "capacity": 2, "dropped": 3}
    assert counters["latest_frame_wins_drops"] == 4
    assert snapshot["last_frame_hash"] == latest.input_frame_hash
    assert memory["current_estimated_pixel_bytes"] <= memory["configured_upper_bound_pixel_bytes"]


def test_non_monotonic_capture_is_rejected_without_mutating_rings() -> None:
    observer = CPUStreamObserver(stream_id="time")
    first = observer.push(_solid(0), captured_epoch=90.0, identity=IDENTITY)

    rejected = observer.push(_solid(255), captured_epoch=90.0, identity=IDENTITY)

    assert rejected.accepted_for_study is False
    assert rejected.reason == "non_monotonic_capture_epoch"
    assert rejected.frame_seq == first.frame_seq + 1
    snapshot = observer.snapshot()
    rings = snapshot["rings"]
    counters = snapshot["counters"]
    assert isinstance(rings, dict)
    assert isinstance(counters, dict)
    assert rings["full_frames"]["size"] == 1
    assert snapshot["last_frame_hash"] == first.input_frame_hash
    assert counters["non_monotonic_rejections"] == 1


def test_reset_clears_history_increments_generation_and_preserves_sequence_monotonicity() -> None:
    observer = CPUStreamObserver(stream_id="reset")
    first = observer.push(_solid(0), captured_epoch=100.0, identity=IDENTITY)

    observer.reset()
    empty = observer.snapshot()
    next_frame = observer.push(_solid(0), captured_epoch=101.0, identity=IDENTITY)

    assert empty["status"] == "idle"
    assert empty["stream_generation"] == 2
    assert next_frame.reason == "manual_reset"
    assert next_frame.stream_generation == 2
    assert next_frame.frame_seq == first.frame_seq + 1
    counters = observer.snapshot()["counters"]
    assert isinstance(counters, dict)
    assert counters["manual_resets"] == 1


def test_health_snapshot_is_json_safe_and_complete() -> None:
    observer = CPUStreamObserver(stream_id="health")
    observer.push(_solid(12), captured_epoch=110.0, identity=IDENTITY)

    snapshot = observer.snapshot()

    assert snapshot["schema_version"] == CPU_STREAM_HEALTH_SCHEMA_VERSION
    assert snapshot["status"] == "healthy"
    assert snapshot["cpu_only"] is True
    assert snapshot["stream_id"] == "health"
    assert snapshot["frame_seq"] == 1
    assert isinstance(snapshot["last_decision"], dict)
    assert snapshot["last_decision"]["schema_version"] == CPU_STREAM_DECISION_SCHEMA_VERSION


def test_frame_validation_enforces_strict_pixel_bound() -> None:
    observer = CPUStreamObserver(CPUStreamConfig(max_frame_pixels=4096))

    with pytest.raises(ValueError, match="max_frame_pixels"):
        observer.push(_solid(0, (65, 65)), captured_epoch=120.0, identity=IDENTITY)
    with pytest.raises(ValueError, match="captured_epoch"):
        observer.push(_solid(0, (64, 64)), captured_epoch=float("nan"), identity=IDENTITY)
    with pytest.raises(TypeError, match="Pillow"):
        observer.push(object(), captured_epoch=120.0, identity=IDENTITY)  # type: ignore[arg-type]
