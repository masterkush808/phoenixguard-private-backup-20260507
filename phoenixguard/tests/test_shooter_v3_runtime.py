import ctypes
import importlib.util
from pathlib import Path
from typing import Any, Callable, NoReturn

import pytest

if not hasattr(ctypes, "windll"):
    pytest.skip("shooter.py runtime is Windows-only", allow_module_level=True)


def _load_shooter():
    module_path = Path(__file__).resolve().parents[1] / "shooter.py"
    spec = importlib.util.spec_from_file_location("_shooter_v3_runtime_test", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _boxes() -> dict[str, dict[str, float]]:
    return {
        "buy_icon": {"x": 0.90, "y": 0.46},
        "sell_icon": {"x": 0.90, "y": 0.52},
        "time_button": {"x": 0.91, "y": 0.25},
        "time_input": {"x": 0.91, "y": 0.25},
        "hourly_input": {"x": 0.78, "y": 0.30},
        "minute_input": {"x": 0.82, "y": 0.30},
        "second_input": {"x": 0.85, "y": 0.30},
    }


def _rect(module: Any, *, width: int = 1000, height: int = 800) -> Any:
    rect = module.RECT()
    rect.left = 0
    rect.top = 0
    rect.right = width
    rect.bottom = height
    return rect


def _now_1000() -> float:
    return 1000.0


def _fail_if_called(message: str) -> Callable[..., NoReturn]:
    def _raiser(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError(message)

    return _raiser


def _save_state_noop(_state: dict[str, Any]) -> None:
    return None


def _window_rect_reader(rect: Any) -> Callable[[int], Any]:
    def _reader(_hwnd: int) -> Any:
        return rect

    return _reader


def _packet(
    *,
    packet_id: str = "pgpkt_1",
    now: float = 1000.0,
    side: str = "BUY",
    final_side: str | None = None,
    frame_id: int = 1,
    capture_count: int = 1,
    state_version: int = 1,
    session_id: str = "pocket-live-8788",
    executable: bool = True,
    state: str = "EXECUTABLE",
    packet_age_ms: int = 100,
    broker_click_safe: bool = False,
) -> dict[str, Any]:
    final = final_side or side
    sequence_context = {
        "sequence_id": f"seq_{packet_id}",
        "session_id": session_id,
        "sequence_index": 0,
        "frame_start": max(1, frame_id - 49),
        "frame_end": frame_id,
        "sequence_length": 50,
        "frames_received": 50,
        "frames_used": 50,
        "candle_count": 50,
        "timeframe": "M5",
        "sequence_signature": f"seqsig_{packet_id}",
        "sequence_confidence": 0.99,
        "global_direction": side,
        "local_direction": side,
        "current_phase": "progression",
        "progression_score": 0.99,
        "progression": [{"stage": "context_confirmed", "direction": side, "confidence": 0.92}],
        "motifs": ["impulse", "pullback"],
        "box_history": [{"label": f"H1 {side}", "bbox": [10, 10, 40, 40], "direction": side}],
        "angle_vectors": [],
        "sniper_zones": [],
        "target_zones": [],
        "invalidation_zones": [],
        "sequence_status": "COMPLETE",
        "frame_range": [max(1, frame_id - 49), frame_id],
        "candle_range": [1, 50],
        "frames_dropped": 0,
        "entry_progression": {
            "progression_stage": "SNIPER_READY",
            "maturity_score": 0.9,
            "progression_velocity": 0.34,
            "continuation_strength": 0.84,
            "exhaustion_risk": 0.12,
        },
        "sequence_history": [{"label": f"H1 {side}", "bbox": [10, 10, 40, 40]}],
    }
    return {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": packet_id,
        "session_id": session_id,
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": frame_id,
        "capture_count": capture_count,
        "state_version": state_version,
        "created_epoch": now - 0.1,
        "valid_until_epoch": now + 2.0,
        "provenance": {
            "frame_id": frame_id,
            "capture_count": capture_count,
            "state_version": state_version,
            "sequence_id": sequence_context["sequence_id"],
            "source_lock_id": f"source_lock_{frame_id}",
            "model_health_id": f"mh_{frame_id}",
            "chart_transform_id": f"ct_{frame_id}",
            "created_epoch_ms": int(round((now - 0.1) * 1000.0)),
            "valid_until_epoch_ms": int(round((now + 2.0) * 1000.0)),
        },
        "sequence_id": sequence_context["sequence_id"],
        "sequence_signature": sequence_context["sequence_signature"],
        "sequence_length": sequence_context["sequence_length"],
        "frames_used": sequence_context["frames_used"],
        "sequence_status": sequence_context["sequence_status"],
        "sequence_confidence": sequence_context["sequence_confidence"],
        "instrument_context": {
            "identity_state": "IDENTITY_CONFIRMED" if broker_click_safe else "IDENTITY_LOCKED_BY_USER_PROFILE",
            "display_symbol": "EUR/GBP OTC",
            "ocr_symbol": "EUR/GBP OTC" if broker_click_safe else "",
            "timeframe": "M5",
            "viewport_hash": "viewport-a",
            "broker_surface_hash": "broker-a",
            "confidence": 0.91,
            "paper_safe": True,
            "broker_click_safe": broker_click_safe,
            "session_id": session_id,
        },
        "symbol_context": {
            "display_symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "session_id": session_id,
        },
        "live_integrity": {
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": f"hash_{frame_id}",
            "previous_frame_hash": f"hash_{frame_id - 1}",
            "packet_age_ms": packet_age_ms,
        },
        "execution": {
            "enabled": executable,
            "state": state,
            "side": side,
            "expiry_seconds": 300,
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
            "time_sequence": {
                "mode": "TYPE_OR_ADJUST",
                "target_seconds": 300,
                "target_text": "00:05:00",
                "steps": [
                    {"action": "focus_time_field"},
                    {"action": "type_time", "value": "00:05:00"},
                    {"action": "confirm_time"},
                ],
            },
        },
        "model_council": {
            "final_state": state,
            "final_side": final,
            "decision_id": f"mc_{packet_id}",
            "maturity_stage": "EXECUTABLE_PACKET" if executable else "TIMING_READINESS",
            "contributors_are_diagnostic": True,
            "sequence_context": sequence_context,
            "sequence_id": sequence_context["sequence_id"],
            "sequence_signature": sequence_context["sequence_signature"],
            "sequence_length": sequence_context["sequence_length"],
            "frames_used": sequence_context["frames_used"],
            "sequence_status": sequence_context["sequence_status"],
            "sequence_confidence": sequence_context["sequence_confidence"],
        },
        "runtime_model_health": {
            "all_required_models_awake": True,
            "council_status": "AWAKE",
        },
        "block_reason": None,
    }


def _prime_second_read(shooter: Any, state: dict[str, Any], now: float = 1000.0) -> None:
    first = _packet(packet_id="prime_1", now=now, frame_id=1, capture_count=1, state_version=1)
    decision = shooter._evaluate_v3_shooter_decision(
        first,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=now,
    )
    assert decision["reason"] == "WAITING_SECOND_LIVE_READ"


def test_fetch_latest_model_council_packet_recovers_from_runtime_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shooter = _load_shooter()
    packet = _packet(packet_id="trace-exec", now=1000.0)
    trace_payload = {
        "status": "ok",
        "endpoints": {
            "execution_latest": {
                "ok": True,
                "payload": {
                    "execution_packet": packet,
                },
            },
            "study_latest": {
                "ok": True,
                "payload": {
                    "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
                    "packet_type": "STUDY_PACKET",
                    "packet_id": "study-only",
                },
            },
        },
    }
    calls: list[str] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return __import__("json").dumps(trace_payload).encode("utf-8")

    def fake_urlopen(req: Any, timeout: float = 0.0) -> Response:
        url = str(req.full_url)
        calls.append(url)
        if "runtime/trace/v3" not in url:
            raise TimeoutError("direct execution endpoint timed out")
        assert timeout == pytest.approx(0.25)
        return Response()

    monkeypatch.setattr(shooter.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(shooter.time, "time", _now_1000)

    recovered = shooter.fetch_latest_model_council_packet(
        "http://127.0.0.1:8793",
        "pocket-live-8788",
        timeout=0.25,
    )

    assert recovered is not None
    assert recovered["packet_id"] == "trace-exec"
    assert any("runtime/trace/v3" in url for url in calls)


def test_load_boxes_prefers_authoritative_user_calibration_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shooter = _load_shooter()
    boxes_path = tmp_path / "808_shooter_boxes.json"
    manifest_path = tmp_path / "user_calibration_manifest.json"
    boxes_path.write_text(
        __import__("json").dumps(
            {
                "buy_icon": {"x": 0.90, "y": 0.46},
                "sell_icon": {"x": 0.90, "y": 0.52},
                "time_button": {"x": 0.91, "y": 0.25},
                "time_input": {"x": 0.91, "y": 0.25},
                "hourly_plus": {"x": 0.78, "y": 0.27},
                "hourly_input": {"x": 0.78, "y": 0.30},
                "hourly_minus": {"x": 0.78, "y": 0.33},
                "minute_plus": {"x": 0.82, "y": 0.27},
                "minute_input": {"x": 0.82, "y": 0.30},
                "minute_minus": {"x": 0.82, "y": 0.33},
                "second_plus": {"x": 0.85, "y": 0.27},
                "second_input": {"x": 0.85, "y": 0.30},
                "second_minus": {"x": 0.85, "y": 0.33},
                "time_300": {"x": 0.15, "y": 0.15},
                "broker_screen": {"x": 0.75, "y": 0.29},
                "final_screen": {"x": 0.50, "y": 0.75},
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        __import__("json").dumps(
            {
                "authoritative_execution_source": True,
                "profiles": {
                    "default": {
                        "layouts": {
                            "default": {
                                "required_targets": {
                                    "buy_button": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "buy_icon",
                                        "point": {"x": 0.91, "y": 0.44},
                                    },
                                    "sell_button": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "sell_icon",
                                        "point": {"x": 0.90, "y": 0.49},
                                    },
                                    "expiry_time_field": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "time_button",
                                        "point": {"x": 0.89, "y": 0.26},
                                    },
                                    "expiry_plus": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "hourly_plus",
                                        "point": {"x": 0.77, "y": 0.26},
                                    },
                                    "expiry_minus": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "hourly_minus",
                                        "point": {"x": 0.78, "y": 0.32},
                                    },
                                    "broker_focus_area": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "broker_screen",
                                        "point": {"x": 0.75, "y": 0.29},
                                    },
                                },
                                "optional_targets": {
                                    "chart_area": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "final_screen",
                                        "point": {"x": 0.50, "y": 0.75},
                                    }
                                },
                                "source_boxes_path": str(boxes_path),
                                "runtime_artifacts": [str(boxes_path)],
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(shooter, "BOXES_FILE", boxes_path)
    monkeypatch.setattr(shooter, "CALIBRATION_MANIFEST_FILE", manifest_path)

    boxes = shooter.load_boxes()

    assert boxes["capabilities"]["authoritative_manifest"] is True
    assert boxes["buy_icon"]["x"] == pytest.approx(0.90)
    assert boxes["buy_button"]["x"] == pytest.approx(0.90)
    assert boxes["sell_icon"]["y"] == pytest.approx(0.52)
    assert boxes["sell_button"]["y"] == pytest.approx(0.52)
    assert boxes["time_button"]["x"] == pytest.approx(0.91)
    assert boxes["time_input"]["x"] == pytest.approx(0.91)
    assert boxes["hourly_input"]["x"] == pytest.approx(0.78)
    assert boxes["minute_input"]["x"] == pytest.approx(0.82)
    assert boxes["second_input"]["x"] == pytest.approx(0.85)
    assert boxes["time_300"]["x"] == pytest.approx(0.15)
    assert boxes["capabilities"]["supplemented_runtime_targets"] == [
        "hourly_input",
        "minute_input",
        "minute_minus",
        "minute_plus",
        "second_input",
        "second_minus",
        "second_plus",
        "time_300",
    ]
    assert boxes["broker_screen"]["x"] == pytest.approx(0.75)
    assert boxes["final_screen"]["x"] == pytest.approx(0.50)
    assert shooter.validate_calibration(boxes, _rect(shooter)) is True


def test_load_boxes_canonicalizes_plural_seconds_runtime_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shooter = _load_shooter()
    boxes_path = tmp_path / "808_shooter_boxes.json"
    manifest_path = tmp_path / "user_calibration_manifest.json"
    boxes_path.write_text(
        __import__("json").dumps(
            {
                "buy_icon": {"x": 0.90, "y": 0.46},
                "sell_icon": {"x": 0.90, "y": 0.52},
                "time_input": {"x": 0.91, "y": 0.25},
                "hourly_input": {"x": 0.78, "y": 0.30},
                "minute_input": {"x": 0.82, "y": 0.30},
                "seconds_input": {"x": 0.85, "y": 0.30},
                "broker_screen": {"x": 0.75, "y": 0.29},
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        __import__("json").dumps(
            {
                "authoritative_execution_source": True,
                "profiles": {
                    "default": {
                        "layouts": {
                            "default": {
                                "required_targets": {
                                    "buy_button": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "buy_icon",
                                        "point": {"x": 0.90, "y": 0.46},
                                    },
                                    "sell_button": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "sell_icon",
                                        "point": {"x": 0.90, "y": 0.52},
                                    },
                                    "expiry_time_field": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "time_input",
                                        "point": {"x": 0.91, "y": 0.25},
                                    },
                                    "broker_focus_area": {
                                        "marked": True,
                                        "status": "USER_CALIBRATED",
                                        "source_key": "broker_screen",
                                        "point": {"x": 0.75, "y": 0.29},
                                    },
                                },
                                "source_boxes_path": str(boxes_path),
                                "runtime_artifacts": [str(boxes_path)],
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(shooter, "BOXES_FILE", boxes_path)
    monkeypatch.setattr(shooter, "CALIBRATION_MANIFEST_FILE", manifest_path)

    boxes = shooter.load_boxes()

    assert boxes["second_input"]["x"] == pytest.approx(0.85)
    assert boxes["second_input"]["manifest_source_key"] == "seconds_input"
    assert "second_input" not in boxes["capabilities"].get("missing_runtime_targets", [])
    assert shooter.validate_calibration(boxes, _rect(shooter)) is True


def test_validate_calibration_rejects_non_alias_duplicate_points() -> None:
    shooter = _load_shooter()
    boxes = {
        "buy_icon": {"x": 0.80, "y": 0.40},
        "sell_icon": {"x": 0.80, "y": 0.40},
        "time_button": {"x": 0.70, "y": 0.20},
    }

    assert shooter.validate_calibration(boxes, _rect(shooter)) is False


def test_validate_calibration_allows_time_button_alias_overlap() -> None:
    shooter = _load_shooter()
    boxes = {
        "buy_icon": {"x": 0.80, "y": 0.40},
        "sell_icon": {"x": 0.80, "y": 0.50},
        "time_button": {"x": 0.70, "y": 0.20},
        "time_input": {"x": 0.70, "y": 0.20},
    }

    assert shooter.validate_calibration(boxes, _rect(shooter)) is True


def test_shooter_refuses_non_v3_packet() -> None:
    shooter = _load_shooter()
    decision = shooter._evaluate_v3_shooter_decision(
        {"signal_id": "legacy", "action": "BUY", "expiry_seconds": 300},
        {},
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )
    assert decision["will_click"] is False
    assert decision["runtime_integrity"] == "RUNTIME_INTEGRITY"
    assert decision["reason"] == "RUNTIME_INTEGRITY: NON_V3_PACKET"


def test_shooter_gates_remain_not_checked_when_packet_missing() -> None:
    shooter = _load_shooter()
    decision = shooter._evaluate_v3_shooter_decision(
        None,
        {},
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )

    assert decision["will_click"] is False
    assert decision["packet_id"] is None
    assert decision["reason"] == "RUNTIME_INTEGRITY: PAYLOAD_MISSING"
    assert decision["gate_1_second_read"] == "NOT_CHECKED"
    assert decision["gate_2_trade_discipline"] == "NOT_CHECKED"
    assert decision["gate_3_model_council"] == "NOT_CHECKED"
    assert decision["calibration"] == "NOT_CHECKED"


def test_shooter_displays_study_packet_without_entering_gates() -> None:
    shooter = _load_shooter()
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "study_1",
        "packet_type": "STUDY_PACKET",
        "session_id": "pocket-live-8788",
        "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
        "model_council": {
            "final_state": "WATCHING",
            "final_side": "SELL",
            "final_execution_score": 0.66,
            "execution_threshold": 0.70,
        },
        "promotion_trace": {
            "candidate_id": "sell_cont_004",
            "candidate_stage": "PREPARING",
            "true_blocker": "FINAL_EXECUTION_SCORE_BELOW_THRESHOLD",
            "next_required": "score +0.04 or timing_ready=true",
            "selected_lane": "LOCAL_BREAKDOWN_CONTINUATION",
            "lane_accepted": False,
        },
        "execution_lane": {
            "name": "LOCAL_BREAKDOWN_CONTINUATION",
            "accepted": False,
            "reason": "lane score below threshold",
        },
    }

    decision = shooter._v3_study_wait_decision(study_packet, now=1000.0)

    assert decision["packet_id"] == "study_1"
    assert decision["packet_type"] == "STUDY_PACKET"
    assert decision["runtime_integrity"] == "WAITING_STUDY_PACKET"
    assert decision["true_blocker"] == "FINAL_EXECUTION_SCORE_BELOW_THRESHOLD"
    assert decision["candidate_stage"] == "PREPARING"
    assert decision["selected_execution_lane"] == "LOCAL_BREAKDOWN_CONTINUATION"
    assert decision["lane_accepted"] is False
    assert "lane=LOCAL_BREAKDOWN_CONTINUATION" in decision["model_council_wait"]
    assert decision["will_click"] is False
    assert decision["gate_1_second_read"] == "NOT_CHECKED"
    assert decision["gate_2_trade_discipline"] == "NOT_CHECKED"
    assert decision["gate_3_model_council"] == "NOT_CHECKED"


def test_shooter_synthesizes_study_packet_from_legacy_council_result() -> None:
    shooter = _load_shooter()
    tracker_payload = {
        "session_id": "pocket-live-8788",
        "state_version": 42,
        "decision_version": 42,
        "model_council_result": {
            "execution": {"enabled": False, "state": "WATCHING"},
            "model_council": {
                "final_state": "WATCHING",
                "final_side": "SELL",
                "arbitration_reason": "WATCHING: blocked_by=TIMING; next_required=timing READY",
                "final_execution_score": 0.64,
                "execution_threshold": 0.70,
            },
            "promotion_trace": {
                "candidate_id": "sell_candidate_001",
                "candidate_stage": "PREPARING",
                "candidate_side": "SELL",
                "true_blocker": "TIMING",
                "next_required": "timing READY",
                "selected_lane": "FAILED_RETEST_ENTRY",
                "lane_accepted": True,
            },
            "execution_lane": {"name": "FAILED_RETEST_ENTRY", "accepted": True},
        },
    }

    study_packet = shooter._extract_model_council_study_packet(tracker_payload)
    assert study_packet is not None
    assert study_packet["packet_id"]
    assert study_packet["packet_type"] == "STUDY_PACKET"
    assert float(study_packet["valid_until_epoch"]) > float(study_packet["created_epoch"])
    assert study_packet["execution"]["side"] == "SELL"
    assert study_packet["promotion_trace"]["true_blocker"] == "TIMING"
    assert study_packet["selected_execution_lane"] == "FAILED_RETEST_ENTRY"
    assert study_packet["promotion_trace"]["lane_accepted"] is True

    decision = shooter._v3_study_wait_decision(study_packet, now=1000.0)
    assert decision["packet_id"] == study_packet["packet_id"]
    assert decision["packet_type"] == "STUDY_PACKET"
    assert decision["runtime_integrity"] == "WAITING_STUDY_PACKET"
    assert decision["side"] == "SELL"


def test_synthesized_study_packet_never_inherits_executable_authority() -> None:
    shooter = _load_shooter()
    tracker_payload = {
        "session_id": "pocket-live-8788",
        "state_version": 1002000,
        "last_capture_epoch": 1002.0,
        "decision_valid_until_epoch": 1010.0,
        "model_council_result": {
            "execution": {"enabled": True, "state": "EXECUTABLE", "side": "BUY"},
            "model_council": {
                "final_state": "EXECUTABLE",
                "final_side": "BUY",
                "final_execution_score": 0.91,
                "execution_threshold": 0.70,
            },
            "promotion_trace": {
                "candidate_id": "buy_candidate_001",
                "candidate_side": "BUY",
                "promotion_result": "EXECUTABLE",
                "selected_lane": "MOMENTUM_ACCEPTANCE_ENTRY",
                "lane_accepted": True,
            },
        },
    }

    study_packet = shooter._current_or_synthesized_model_council_study_packet(
        tracker_payload,
        now=1002.0,
        max_packet_age_seconds=8.0,
    )
    assert study_packet is not None
    assert study_packet["packet_type"] == "STUDY_PACKET"
    assert study_packet["execution"]["enabled"] is False
    assert study_packet["execution"]["state"] == "WATCHING"
    assert study_packet["model_council"]["final_state"] == "WATCHING"
    assert study_packet["promotion_trace"]["true_blocker"] == "EXECUTION_PACKET_NOT_PUBLISHED"
    assert "PG_EXECUTION_PACKET_V3" in study_packet["promotion_trace"]["next_required"]

    decision = shooter._v3_study_wait_decision(study_packet, now=1002.0)
    assert decision["execution_state"] == "WATCHING"
    assert decision["runtime_integrity"] == "WAITING_STUDY_PACKET"
    assert decision["will_click"] is False


def test_shooter_rejects_stale_study_packet_for_current_display() -> None:
    shooter = _load_shooter()
    stale_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "study_old",
        "packet_type": "STUDY_PACKET",
        "created_epoch": 1000.0,
        "valid_until_epoch": 1001.0,
        "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
        "model_council": {"final_state": "WATCHING", "final_side": "BUY"},
        "promotion_trace": {"denied_at": "TIMING_WAIT", "next_required": "fresh packet"},
    }
    fresh_packet = dict(stale_packet)
    fresh_packet["packet_id"] = "study_fresh"
    fresh_packet["created_epoch"] = 1000.0
    fresh_packet["valid_until_epoch"] = 1010.0

    assert shooter._v3_study_packet_is_current(stale_packet, now=1002.0, max_packet_age_seconds=8.0) is False
    assert shooter._v3_study_packet_is_current(fresh_packet, now=1002.0, max_packet_age_seconds=8.0) is True


def test_shooter_synthesizes_current_study_when_nested_packet_is_stale() -> None:
    shooter = _load_shooter()
    tracker_payload = {
        "session_id": "pocket-live-8788",
        "state_version": 1002000,
        "last_capture_epoch": 1002.0,
        "decision_valid_until_epoch": 1010.0,
        "model_council_study_packet": {
            "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
            "packet_id": "old_study",
            "packet_type": "STUDY_PACKET",
            "created_epoch": 1000.0,
            "valid_until_epoch": 1001.0,
            "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
            "model_council": {"final_state": "WATCHING", "final_side": "SELL"},
            "promotion_trace": {"denied_at": "OLD", "next_required": "fresh state"},
        },
        "model_council_result": {
            "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
            "model_council": {
                "final_state": "WATCHING",
                "final_side": "BUY",
                "arbitration_reason": "timing wait",
            },
            "promotion_trace": {
                "candidate_side": "BUY",
                "true_blocker": "TIMING_WAIT",
                "next_required": "failed retest confirmation",
            },
        },
    }

    packet = shooter._current_or_synthesized_model_council_study_packet(
        tracker_payload,
        now=1002.0,
        max_packet_age_seconds=8.0,
    )

    assert packet is not None
    assert packet["packet_id"] != "old_study"
    assert packet["execution"]["side"] == "BUY"
    assert packet["created_epoch"] == 1002.0
    assert packet["valid_until_epoch"] == 1010.0


def test_shooter_writes_handshake_for_study_packet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shooter = _load_shooter()
    handshake_path = tmp_path / "shooter_handshake.json"
    monkeypatch.setattr(shooter, "_SHOOTER_RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(shooter, "_SHOOTER_HANDSHAKE_PATH", handshake_path)
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "study_handshake",
        "packet_type": "STUDY_PACKET",
        "session_id": "pocket-live-8788",
        "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
        "model_council": {"final_state": "WATCHING", "final_side": "BUY"},
        "promotion_trace": {
            "true_blocker": "FINAL_EXECUTION_SCORE_BELOW_THRESHOLD",
            "selected_lane": "MOMENTUM_ACCEPTANCE_ENTRY",
            "lane_accepted": False,
        },
        "execution_lane": {"name": "MOMENTUM_ACCEPTANCE_ENTRY", "accepted": False},
    }
    decision = shooter._v3_study_wait_decision(study_packet, now=1000.0)

    shooter._write_shooter_handshake(
        session_id="pocket-live-8788",
        base_url="http://127.0.0.1:8793",
        decision=decision,
        packet=study_packet,
        tracker_snapshot={"state_version": 7, "decision_version": 7, "status": "running"},
        selected_window_hwnd=132820,
        preferred_window_hwnd=132820,
    )

    payload = __import__("json").loads(handshake_path.read_text(encoding="utf-8"))
    assert payload["packet_id"] == "study_handshake"
    assert payload["packet_type"] == "STUDY_PACKET"
    assert payload["study_packet_present"] is True
    assert payload["execution_packet_present"] is False
    assert payload["gate_1_second_read"] == "NOT_CHECKED"
    assert payload["selected_execution_lane"] == "MOMENTUM_ACCEPTANCE_ENTRY"
    assert payload["lane_accepted"] is False
    assert payload["selected_window_hwnd"] == 132820
    assert payload["preferred_window_hwnd"] == 132820
    assert payload["window_matches_preferred"] is True


def test_extract_model_council_packet_ignores_expired_snapshot_execution_packet() -> None:
    shooter = _load_shooter()
    expired_packet = _packet(packet_id="expired_snapshot_exec", now=1000.0)
    expired_packet["valid_until_epoch"] = 999.0
    expired_packet["valid_until_epoch_sec"] = 999.0

    payload = {
        "session_id": "pocket-live-8788",
        "execution_packet": expired_packet,
        "model_council_packet": expired_packet,
    }

    assert shooter._extract_model_council_packet(payload, now=1000.0) is None


def test_extract_model_council_packet_rejects_schema_only_study_object() -> None:
    shooter = _load_shooter()
    study_shaped = _packet(packet_id="schema_only_study", now=1000.0)
    study_shaped["packet_type"] = "STUDY_PACKET"
    study_shaped["execution"]["side"] = None
    study_shaped["model_council"]["final_side"] = "BUY"

    payload = {"execution_packet": study_shaped}

    assert shooter._extract_model_council_packet(payload, now=1000.0) is None


def test_extract_model_council_packet_rejects_demoted_execution_root_without_side() -> None:
    shooter = _load_shooter()
    demoted = _packet(packet_id="demoted_exec_root", now=1000.0)
    demoted["execution"]["enabled"] = False
    demoted["execution"]["state"] = "WATCHING"
    demoted["execution"]["side"] = None
    demoted["model_council"]["final_state"] = "WATCHING"
    demoted["model_council"]["final_side"] = None
    demoted["promotion_trace"] = {
        "packet_result": "STUDY_PACKET_PUBLISHED",
        "denied_at": "SIGNAL_THESIS_V3_COUNTERTREND_BLOCK",
    }

    assert shooter._extract_model_council_packet({"model_council_result": demoted}, now=1000.0) is None


def test_v3_packet_side_uses_explicit_execution_side_for_second_read() -> None:
    shooter = _load_shooter()
    packet = _packet(packet_id="strict_side", side="BUY", final_side="BUY")
    assert shooter._v3_packet_side(packet) == "BUY"

    packet["model_council"]["final_side"] = "SELL"
    assert shooter._v3_packet_side(packet) == "BUY"

    packet["model_council"]["final_side"] = "BUY"
    packet["execution"]["side"] = None
    assert shooter._v3_packet_side(packet) is None


def test_study_handshake_does_not_carry_previous_action_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shooter = _load_shooter()
    handshake_path = tmp_path / "shooter_handshake.json"
    monkeypatch.setattr(shooter, "_SHOOTER_RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(shooter, "_SHOOTER_HANDSHAKE_PATH", handshake_path)
    monkeypatch.setattr(
        shooter,
        "_last_action_sequence_result",
        shooter.ActionSequenceResult(
            overall="FAILED",
            reason="WINDOW_ACTIVATION_FAILED",
            packet_id="old_exec_packet",
            side="SELL",
            expiry_seconds=300,
            state="ABORT_BEFORE_SIDE_CLICK",
        ),
    )
    study_packet = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_id": "study_waiting_packet",
        "packet_type": "STUDY_PACKET",
        "session_id": "pocket-live-8788",
        "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
        "model_council": {"final_state": "WATCHING", "final_side": "SELL"},
    }
    decision = shooter._v3_study_wait_decision(study_packet, now=1000.0)

    shooter._write_shooter_handshake(
        session_id="pocket-live-8788",
        base_url="http://127.0.0.1:8793",
        decision=decision,
        packet=study_packet,
        tracker_snapshot={"state_version": 7, "decision_version": 7, "status": "running"},
    )

    payload = __import__("json").loads(handshake_path.read_text(encoding="utf-8"))
    assert payload["packet_id"] == "study_waiting_packet"
    assert payload["packet_type"] == "STUDY_PACKET"
    assert payload["action_sequence"] is None


def test_shooter_refuses_first_read() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(now=1000.0),
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )
    assert decision["will_click"] is False
    assert decision["gate_1_second_read"] == "WAIT"
    assert decision["reason"] == "WAITING_SECOND_LIVE_READ"


def test_shooter_accepts_backend_confirmed_live_read_without_extra_poll() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    packet = _packet(packet_id="backend-live-proof", now=1000.0, frame_id=42, capture_count=77, state_version=9)

    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        tracker_snapshot={
            "session_id": "pocket-live-8788",
            "symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "display_frame_id": 42,
            "capture_count": 77,
            "state_version": 9,
            "latest_signal": {
                "live_integrity": {
                    "input_frame_hash": "hash_42",
                }
            },
        },
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )

    assert decision["will_click"] is True
    assert decision["gate_1_second_read"] == "PASS"
    assert decision["gate_2_trade_discipline"] == "PASS"
    assert decision["gate_3_model_council"] == "PASS"
    assert state["v3_second_live_read_confirmed"]["input_frame_hash"] == "hash_42"


def test_shooter_accepts_extracted_backend_packet_despite_stale_baseline() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {
        "v3_second_live_read_baseline": {
            "session_id": "pocket-live-8788",
            "symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "frame_id": 1,
            "capture_count": 1,
            "state_version": 1,
            "packet_id": "old-baseline",
            "side": "BUY",
            "input_frame_hash": "hash_1",
            "seen_at": 900.0,
        }
    }
    extracted = shooter._extract_model_council_packet(
        {
            "latest_signal": _packet(
                packet_id="fresh-backend-packet",
                now=1000.0,
                frame_id=45,
                capture_count=91,
                state_version=12,
            )
        },
        now=1000.0,
    )

    assert extracted is not None
    assert extracted["_backend_confirmed_execution_packet"] is True
    decision = shooter._evaluate_v3_shooter_decision(
        extracted,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )

    assert decision["will_click"] is True
    assert decision["gate_1_second_read"] == "PASS"
    assert state["v3_second_live_read_confirmed"]["packet_id"] == "fresh-backend-packet"
    assert state["v3_second_live_read_confirmed"]["confirmation_source"].startswith("runtime_trace")


def test_shooter_accepts_second_read_executable_packet() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(packet_id="pgpkt_2", now=1000.2, frame_id=2, capture_count=2, state_version=2),
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is True
    assert decision["gate_1_second_read"] == "PASS"
    assert decision["gate_2_trade_discipline"] == "PASS"
    assert decision["gate_3_model_council"] == "PASS"
    assert decision["calibration"] == "VALID"
    assert decision["expiry_seconds"] == 300


def test_shooter_accepts_second_read_from_tracker_live_counters_for_same_packet() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    packet = _packet(packet_id="pgpkt_same_current_packet", now=1000.0, frame_id=10, capture_count=20, state_version=30)

    first = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        tracker_snapshot={
            "session_id": "pocket-live-8788",
            "display_frame_id": 100,
            "capture_count": 200,
            "state_version": 0,
        },
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )

    assert first["will_click"] is False
    assert first["reason"] == "WAITING_SECOND_LIVE_READ"

    second = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        tracker_snapshot={
            "session_id": "pocket-live-8788",
            "display_frame_id": 101,
            "capture_count": 201,
            "state_version": 0,
        },
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )

    assert second["will_click"] is True
    assert second["gate_1_second_read"] == "PASS"
    assert second["calibration"] == "VALID"


def test_v3_packet_does_not_use_legacy_parse_trade_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    monkeypatch.setattr(
        shooter,
        "parse_trade_signal",
        _fail_if_called("legacy parser must not run for V3 packets"),
    )

    decision = shooter._evaluate_v3_shooter_decision(
        _packet(packet_id="pgpkt_no_legacy_parser", now=1000.2, frame_id=2, capture_count=2, state_version=2),
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )

    assert decision["gate_1_second_read"] == "PASS"


def test_shooter_resets_stale_second_read_baseline() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {
        "v3_second_live_read_baseline": {
            "session_id": "pocket-live-8788",
            "symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "frame_id": 1,
            "capture_count": 1,
            "state_version": 1,
            "packet_id": "old-baseline",
            "side": "BUY",
            "input_frame_hash": "hash_1",
            "seen_at": 900.0,
        }
    }
    packet = _packet(packet_id="fresh-first-read", now=1000.0, frame_id=2, capture_count=2, state_version=2)

    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )

    assert decision["will_click"] is False
    assert decision["reason"] == "WAITING_SECOND_LIVE_READ_STALE_BASELINE_RESET"
    assert state["v3_second_live_read_baseline"]["packet_id"] == "fresh-first-read"


def test_signal_mode_defaults_to_live_disabled() -> None:
    shooter = _load_shooter()

    args = shooter.build_parser().parse_args(["signal", "--session-id", "pocket-live"])

    assert args.shooter_mode == "LIVE_DISABLED"
    assert args.expiry == 0
    assert "amount" not in vars(args)


def test_signal_mode_accepts_live_ready_choice() -> None:
    shooter = _load_shooter()

    args = shooter.build_parser().parse_args(["signal", "--session-id", "pocket-live", "--shooter-mode", "LIVE_READY"])

    assert args.shooter_mode == "LIVE_READY"


def test_startup_test_entry_rejects_non_calibration_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    args = shooter.build_parser().parse_args(
        ["signal", "--session-id", "pocket-live-8788", "--test-signal", "--shooter-mode", "LIVE_READY"]
    )
    monkeypatch.setattr(shooter, "execute_trade", _fail_if_called("must not click"))

    clicked = shooter._run_startup_test_entry(
        args,
        hwnd=1,
        boxes=_boxes(),
        shooter_mode=shooter.shooter_modes.ShooterMode.LIVE_READY,
        state={},
    )

    assert clicked is False


def test_startup_test_entry_runs_time_only_in_calibration_test(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    args = shooter.build_parser().parse_args(
        [
            "signal",
            "--session-id",
            "pocket-live-8788",
            "--base-url",
            "http://127.0.0.1:8793",
            "--test-signal",
            "--shooter-mode",
            "CALIBRATION_TEST",
        ]
    )
    clicks: list[tuple[str, int, dict[str, Any]]] = []
    monkeypatch.setenv("PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS", "1")

    def _generate_test_signal(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "signal_id": "startup-test-1",
            "actionable": True,
            "execution_action": "BUY",
            "expiry_seconds": 300,
            "focus_timeframe": "M5",
            "market": "EUR/GBP OTC",
        }

    def _execute_v3_packet_trade(
        _hwnd: int,
        _boxes_arg: dict[str, Any],
        packet: dict[str, Any],
        **kwargs: Any,
    ) -> bool:
        execution = packet["execution"]
        clicks.append(
            (
                str(execution["side"]),
                int(execution["expiry_seconds"]),
                kwargs,
            )
        )
        return True

    monkeypatch.setattr(
        shooter,
        "generate_test_signal",
        _generate_test_signal,
    )
    monkeypatch.setattr(
        shooter,
        "execute_trade",
        _fail_if_called("startup test must use sequencer path"),
    )
    monkeypatch.setattr(
        shooter,
        "execute_v3_packet_trade",
        _execute_v3_packet_trade,
    )
    monkeypatch.setattr(shooter, "_three_gate_save_state", _save_state_noop)

    state: dict[str, Any] = {}
    clicked = shooter._run_startup_test_entry(
        args,
        hwnd=1,
        boxes=_boxes(),
        shooter_mode=shooter.shooter_modes.ShooterMode.CALIBRATION_TEST,
        state=state,
    )

    assert clicked is True
    assert len(clicks) == 1
    side, expiry, kwargs = clicks[0]
    assert (side, expiry) == ("BUY", 300)
    assert kwargs["allow_live_clicks"] is True
    assert kwargs["action_speed"] == "balanced"
    assert kwargs["record_action_evidence"] is False
    assert kwargs["live_behavior_validation"] is True
    assert kwargs["behavior_report_mode"] == "CALIBRATION_TEST"
    assert kwargs["session_id"] == "pocket-live-8788"
    assert kwargs["time_button_wait_override_ms"] is None
    assert kwargs["skip_side_click"] is True
    assert "v3_trade_count" not in state
    assert "v3_executed_packet_keys" not in state


def test_startup_calibration_test_can_force_1h45m_typed_sequence_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    args = shooter.build_parser().parse_args(
        [
            "signal",
            "--session-id",
            "pocket-live-8788",
            "--base-url",
            "http://127.0.0.1:8793",
            "--test-signal",
            "--shooter-mode",
            "CALIBRATION_TEST",
            "--calibration-test-side",
            "SELL",
            "--calibration-test-expiry",
            "6300",
            "--calibration-test-time-fill-wait",
            "5",
            "--record-action-evidence",
        ]
    )
    observed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    monkeypatch.setenv("PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS", "1")

    def _generate_test_signal(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "signal_id": "startup-test-1",
            "actionable": True,
            "execution_action": "BUY",
            "expiry_seconds": 300,
        }

    def _execute_v3_packet_trade(
        _hwnd: int,
        _boxes_arg: dict[str, Any],
        packet: dict[str, Any],
        **kwargs: Any,
    ) -> bool:
        observed.append((packet, kwargs))
        return True

    monkeypatch.setattr(
        shooter,
        "generate_test_signal",
        _generate_test_signal,
    )
    monkeypatch.setattr(shooter, "execute_trade", _fail_if_called("legacy click path used"))
    monkeypatch.setattr(shooter, "execute_v3_packet_trade", _execute_v3_packet_trade)

    state: dict[str, Any] = {}
    monkeypatch.setattr(shooter, "_three_gate_save_state", _save_state_noop)

    clicked = shooter._run_startup_test_entry(
        args,
        hwnd=1,
        boxes=_boxes(),
        shooter_mode=shooter.shooter_modes.ShooterMode.CALIBRATION_TEST,
        state=state,
    )

    assert clicked is True
    packet, kwargs = observed[0]
    assert packet["execution"]["side"] == "SELL"
    assert packet["execution"]["expiry_seconds"] == 6300
    assert packet["execution"]["time_sequence"]["target_text"] == "01:45:00"
    assert kwargs["time_button_wait_override_ms"] == 5000
    assert kwargs["record_action_evidence"] is True
    assert kwargs["skip_side_click"] is True
    assert "v3_trade_count" not in state


def test_startup_calibration_test_runs_time_only_when_trade_discipline_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    args = shooter.build_parser().parse_args(
        [
            "signal",
            "--session-id",
            "pocket-live-8788",
            "--test-signal",
            "--shooter-mode",
            "CALIBRATION_TEST",
        ]
    )
    monkeypatch.setenv("PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS", "1")
    calls: list[dict[str, Any]] = []

    def _generate_test_signal(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "signal_id": "startup-test-locked",
            "actionable": True,
            "execution_action": "BUY",
            "expiry_seconds": 300,
        }

    def _execute_v3_packet_trade(*_args: object, **kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(
        shooter,
        "generate_test_signal",
        _generate_test_signal,
    )
    monkeypatch.setattr(shooter, "execute_v3_packet_trade", _execute_v3_packet_trade)
    state = {"v3_locked_until": shooter.time.time() + 1200}

    clicked = shooter._run_startup_test_entry(
        args,
        hwnd=1,
        boxes=_boxes(),
        shooter_mode=shooter.shooter_modes.ShooterMode.CALIBRATION_TEST,
        state=state,
    )

    assert clicked is True
    assert calls[0]["skip_side_click"] is True
    assert state["v3_locked_until"] > shooter.time.time()


def test_startup_test_entry_waits_for_fresh_bias_before_calibration_click(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    args = shooter.build_parser().parse_args(
        [
            "signal",
            "--session-id",
            "pocket-live-8788",
            "--base-url",
            "http://127.0.0.1:8793",
            "--test-signal",
            "--test-signal-timeout",
            "10",
            "--test-signal-poll",
            "0.01",
            "--shooter-mode",
            "CALIBRATION_TEST",
        ]
    )
    generated = iter(
        [
            {
                "signal_id": "startup-waiting-1",
                "status": "TEST_WAITING_FOR_PHOENIX_BIAS",
                "actionable": False,
                "execution_action": "HOLD",
                "expiry_seconds": 30,
            },
            {
                "signal_id": "startup-test-2",
                "actionable": True,
                "execution_action": "SELL",
                "expiry_seconds": 60,
                "focus_timeframe": "M1",
                "market": "CAD/JPY OTC",
            },
        ]
    )
    clicks: list[tuple[str, int]] = []
    sleeps: list[float] = []
    monkeypatch.setenv("PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS", "1")

    def _generate_test_signal(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return next(generated)

    def _sleep(seconds: float) -> None:
        sleeps.append(float(seconds))

    def _execute_v3_packet_trade(
        _hwnd: int,
        _boxes_arg: dict[str, Any],
        packet: dict[str, Any],
        **_kwargs: object,
    ) -> bool:
        execution = packet["execution"]
        clicks.append((str(execution["side"]), int(execution["expiry_seconds"])))
        return True

    monkeypatch.setattr(shooter, "generate_test_signal", _generate_test_signal)
    monkeypatch.setattr(shooter.time, "sleep", _sleep)
    monkeypatch.setattr(
        shooter,
        "execute_trade",
        _fail_if_called("startup test must use sequencer path"),
    )
    monkeypatch.setattr(
        shooter,
        "execute_v3_packet_trade",
        _execute_v3_packet_trade,
    )
    monkeypatch.setattr(shooter, "_three_gate_save_state", _save_state_noop)

    state: dict[str, Any] = {}
    clicked = shooter._run_startup_test_entry(
        args,
        hwnd=1,
        boxes=_boxes(),
        shooter_mode=shooter.shooter_modes.ShooterMode.CALIBRATION_TEST,
        state=state,
    )

    assert clicked is True
    assert sleeps == [0.05]
    assert clicks == [("SELL", 60)]
    assert "v3_trade_count" not in state


def test_paper_execution_records_without_click(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    record_path = tmp_path / "paper.jsonl"
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(shooter.shooter_modes, "PAPER_EXECUTION_LOG", record_path)
    monkeypatch.setattr(shooter, "execute_v3_packet_trade", _fail_if_called("must not click"))
    monkeypatch.setattr(shooter, "click_trade_button", _fail_if_called("must not click"))

    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="paper-1", frame_id=2, capture_count=2, state_version=2)
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is True

    result = shooter._v3_apply_shooter_mode(
        1,
        _boxes(),
        packet,
        decision,
        state,
        shooter.shooter_modes.ShooterMode.PAPER_EXECUTION,
        now=1000.3,
    )

    assert result.reason == "PAPER_EXECUTION_RECORDED"
    assert record_path.exists()
    record = record_path.read_text(encoding="utf-8")
    assert '"packet_id": "paper-1"' in record
    assert shooter._v3_already_executed(state, packet) is True


def test_dry_run_records_click_plan_without_broker_click(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    record_path = tmp_path / "dry.jsonl"
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    rect = shooter.RECT()
    rect.left = 0
    rect.top = 0
    rect.right = 1000
    rect.bottom = 800
    monkeypatch.setattr(shooter.shooter_modes, "DRY_RUN_CLICK_LOG", record_path)
    monkeypatch.setattr(shooter, "get_window_rect", _window_rect_reader(rect))
    monkeypatch.setattr(shooter, "execute_v3_packet_trade", _fail_if_called("must not click"))
    monkeypatch.setattr(shooter, "click_trade_button", _fail_if_called("must not click"))

    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="dry-1", frame_id=2, capture_count=2, state_version=2)
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is True

    result = shooter._v3_apply_shooter_mode(
        1,
        _boxes(),
        packet,
        decision,
        state,
        shooter.shooter_modes.ShooterMode.DRY_RUN_CLICK,
        now=1000.3,
    )

    assert result.reason == "DRY_RUN_CLICK_RECORDED"
    assert record_path.exists()
    record = record_path.read_text(encoding="utf-8")
    assert '"buy_icon"' in record
    assert shooter._v3_already_executed(state, packet) is True


def test_v3_live_broker_click_function_defaults_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    monkeypatch.delenv(shooter.LIVE_BROKER_CLICK_ENV, raising=False)
    monkeypatch.setattr(shooter, "execute_trade", _fail_if_called("must not click"))

    assert shooter.execute_v3_packet_trade(1, _boxes(), _packet()) is False


def test_live_ready_blocks_without_env_even_after_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    record_path = tmp_path / "live_ready.jsonl"
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(shooter.shooter_modes, "LIVE_READY_LOG", record_path)
    monkeypatch.delenv(shooter.LIVE_BROKER_CLICK_ENV, raising=False)
    rect = shooter.RECT()
    rect.left = 0
    rect.top = 0
    rect.right = 1000
    rect.bottom = 800
    monkeypatch.setattr(shooter, "get_window_rect", _window_rect_reader(rect))
    monkeypatch.setattr(shooter, "execute_v3_packet_trade", _fail_if_called("must not click"))

    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="live-ready-no-env", frame_id=2, capture_count=2, state_version=2, broker_click_safe=True)
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )

    result = shooter._v3_apply_shooter_mode(
        1,
        _boxes(),
        packet,
        decision,
        state,
        shooter.shooter_modes.ShooterMode.LIVE_READY,
        now=1000.3,
    )

    assert result.reason.startswith("LIVE_READY_ENV_NOT_ARMED")
    assert record_path.exists()


def test_live_ready_blocks_when_broker_identity_not_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    record_path = tmp_path / "live_ready.jsonl"
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(shooter.shooter_modes, "LIVE_READY_LOG", record_path)
    monkeypatch.setenv(shooter.LIVE_BROKER_CLICK_ENV, "1")
    rect = shooter.RECT()
    rect.left = 0
    rect.top = 0
    rect.right = 1000
    rect.bottom = 800
    monkeypatch.setattr(shooter, "get_window_rect", _window_rect_reader(rect))
    monkeypatch.setattr(shooter, "execute_v3_packet_trade", _fail_if_called("must not click"))

    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="live-ready-identity", frame_id=2, capture_count=2, state_version=2, broker_click_safe=False)
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )

    result = shooter._v3_apply_shooter_mode(
        1,
        _boxes(),
        packet,
        decision,
        state,
        shooter.shooter_modes.ShooterMode.LIVE_READY,
        now=1000.3,
    )

    assert result.reason == "LIVE_READY_REHEARSAL_BLOCKED:INSTRUMENT_CONTEXT_NOT_BROKER_CLICK_SAFE"


def test_live_ready_clicks_only_when_env_identity_and_rehearsal_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    record_path = tmp_path / "live_ready.jsonl"
    clicked: list[str] = []
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(shooter.shooter_modes, "LIVE_READY_LOG", record_path)
    monkeypatch.setenv(shooter.LIVE_BROKER_CLICK_ENV, "1")
    rect = shooter.RECT()
    rect.left = 0
    rect.top = 0
    rect.right = 1000
    rect.bottom = 800
    def _execute_v3_packet_trade(
        _hwnd: int,
        _boxes_arg: dict[str, Any],
        packet: dict[str, Any],
        allow_live_clicks: bool = False,
    ) -> bool:
        clicked.append(str(packet["packet_id"]))
        return True

    monkeypatch.setattr(shooter, "get_window_rect", _window_rect_reader(rect))
    monkeypatch.setattr(shooter, "execute_v3_packet_trade", _execute_v3_packet_trade)

    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="live-ready-click", frame_id=2, capture_count=2, state_version=2, broker_click_safe=True)
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )

    result = shooter._v3_apply_shooter_mode(
        1,
        _boxes(),
        packet,
        decision,
        state,
        shooter.shooter_modes.ShooterMode.LIVE_READY,
        now=1000.3,
    )

    assert result.reason == "LIVE_READY_CLICK_SENT"
    assert clicked == ["live-ready-click"]
    assert shooter._v3_already_executed(state, packet) is True


def test_live_ready_record_exposes_action_sequence_for_burn_monitor(tmp_path: Path) -> None:
    shooter = _load_shooter()
    record_path = tmp_path / "live_ready.jsonl"
    action_sequence = {
        "overall": "PASS",
        "clicked": True,
        "reason": "ACTION_SEQUENCE_COMPLETE",
        "packet_id": "live-ready-record-action",
    }

    result = shooter.shooter_modes.record_live_ready(
        _packet(packet_id="live-ready-record-action", broker_click_safe=True),
        {"reason": "ready"},
        clicked=True,
        reason="LIVE_READY_CLICK_SENT",
        rehearsal={"ready": True, "action_sequence": action_sequence},
        path=record_path,
        now=1000.0,
    )

    payload = __import__("json").loads(record_path.read_text(encoding="utf-8"))
    assert result.reason == "LIVE_READY_CLICK_SENT"
    assert payload["clicked"] is True
    assert payload["action_sequence"]["clicked"] is True
    assert payload["action_sequence_overall"] == "PASS"
    assert payload["action_sequence_reason"] == "ACTION_SEQUENCE_COMPLETE"


def test_find_pocket_option_window_prefers_locked_hwnd(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    windows = [
        (1182268, "The Most Innovative Trading Platform and 30 more pages - Personal - Microsoft Edge", "Chrome_WidgetWin_1"),
        (132820, "The Most Innovative Trading Platform and 30 more pages - Personal - Microsoft Edge", "Chrome_WidgetWin_1"),
    ]

    def fake_list_visible_windows(query: str | None = None) -> list[tuple[int, str, str]]:
        if not query:
            return list(windows)
        lowered = query.lower()
        return [row for row in windows if lowered in row[1].lower()]

    monkeypatch.setattr(shooter, "list_visible_windows", fake_list_visible_windows)

    assert shooter.find_pocket_option_window("The Most Innovative Trading Platform", preferred_hwnd=132820) == 132820


def test_calibration_test_highlights_coordinates_without_click(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    record_path = tmp_path / "calibration.jsonl"
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    rect = shooter.RECT()
    rect.left = 0
    rect.top = 0
    rect.right = 1000
    rect.bottom = 800
    highlighted: list[int] = []
    monkeypatch.setattr(shooter.shooter_modes, "CALIBRATION_TEST_LOG", record_path)
    def _show_box_preview(hwnd: int, _boxes_arg: dict[str, Any]) -> None:
        highlighted.append(hwnd)

    monkeypatch.setattr(shooter, "get_window_rect", _window_rect_reader(rect))
    monkeypatch.setattr(shooter, "show_box_preview", _show_box_preview)
    monkeypatch.setattr(shooter, "click_trade_button", _fail_if_called("must not click"))

    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="cal-1", frame_id=2, capture_count=2, state_version=2)
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )

    result = shooter._v3_apply_shooter_mode(
        123,
        _boxes(),
        packet,
        decision,
        state,
        shooter.shooter_modes.ShooterMode.CALIBRATION_TEST,
        now=1000.3,
    )

    assert result.reason == "CALIBRATION_TEST_RECORDED"
    assert highlighted == [123]
    assert record_path.exists()


def test_shooter_refuses_stale_packet() -> None:
    shooter = _load_shooter()
    stale = _packet(now=1000.0, packet_age_ms=5000)
    decision = shooter._evaluate_v3_shooter_decision(
        stale,
        {},
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.0,
        max_packet_age_seconds=2.0,
    )
    assert decision["will_click"] is False
    assert decision["runtime_integrity"] == "RUNTIME_INTEGRITY"
    assert decision["reason"] == "RUNTIME_INTEGRITY: PACKET_STALE"


def test_shooter_runtime_integrity_respects_explicit_execution_ttl() -> None:
    shooter = _load_shooter()
    packet = _packet(now=1000.0, packet_age_ms=45_000)
    packet["ttl_sec"] = 60.0
    packet["valid_for_seconds"] = 60.0
    packet["valid_until_epoch"] = 1060.0
    packet["valid_until_epoch_sec"] = 1060.0

    ok, reason = shooter._v3_runtime_integrity_check(
        packet,
        expected_session_id="pocket-live-8788",
        now=1045.0,
        max_packet_age_seconds=8.0,
    )

    assert ok is True
    assert reason == "RUNTIME_INTEGRITY: PASS"


def test_shooter_runtime_integrity_rejects_packet_beyond_explicit_ttl() -> None:
    shooter = _load_shooter()
    packet = _packet(now=1000.0, packet_age_ms=65_000)
    packet["ttl_sec"] = 60.0
    packet["valid_for_seconds"] = 60.0
    packet["valid_until_epoch"] = 1070.0
    packet["valid_until_epoch_sec"] = 1070.0

    ok, reason = shooter._v3_runtime_integrity_check(
        packet,
        expected_session_id="pocket-live-8788",
        now=1065.0,
        max_packet_age_seconds=8.0,
    )

    assert ok is False
    assert reason == "RUNTIME_INTEGRITY: PACKET_STALE"


def test_shooter_refuses_session_mismatch() -> None:
    shooter = _load_shooter()
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(session_id="other-session"),
        {},
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )
    assert decision["will_click"] is False
    assert decision["reason"] == "RUNTIME_INTEGRITY: SESSION_ID_MISMATCH"


def test_shooter_refuses_side_mismatch() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(packet_id="pgpkt_2", side="BUY", final_side="SELL", frame_id=2, capture_count=2, state_version=2),
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["gate_3_model_council"] == "FAIL"
    assert decision["reason"] == "MODEL_COUNCIL_SIDE_MISMATCH"


def test_shooter_refuses_non_executable_packet() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(packet_id="watching", executable=False, state="WATCHING", frame_id=2, capture_count=2, state_version=2),
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["reason"] == "MODEL_COUNCIL_NOT_EXECUTABLE"


def test_shooter_refuses_missing_time_sequence() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="missing-time", frame_id=2, capture_count=2, state_version=2)
    del packet["execution"]["time_sequence"]
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["reason"] == "MODEL_COUNCIL_TIME_SEQUENCE_MISSING"


def test_shooter_refuses_wrong_time_field() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="wrong-time-field", frame_id=2, capture_count=2, state_version=2)
    packet["execution"]["time_sequence"]["steps"][0] = {"action": "focus_field", "field": "amount"}
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["reason"] == "MODEL_COUNCIL_TIME_SEQUENCE_WRONG_FIELD"


def test_shooter_refuses_time_sequence_target_mismatch() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="wrong-target", frame_id=2, capture_count=2, state_version=2)
    packet["execution"]["time_sequence"]["target_text"] = "00:04:00"
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["reason"] == "MODEL_COUNCIL_TIME_SEQUENCE_TARGET_MISMATCH"


def test_shooter_refuses_fallback_expiry_source_before_ready() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="fallback-expiry", frame_id=2, capture_count=2, state_version=2)
    packet["expiry_source"] = "timeframe_fallback"

    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )

    assert decision["will_click"] is False
    assert decision["packet_validation"] == "FAIL"
    assert decision["reason"] == "PACKET_VALIDATION:FALLBACK_EXPIRY_SOURCE"


def test_shooter_refuses_uncalibrated_buy_sell() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    bad_boxes = {"buy_icon": {"x": 0.8, "y": 0.4}, "time_button": {"x": 0.8, "y": 0.2}}
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(packet_id="pgpkt_2", frame_id=2, capture_count=2, state_version=2),
        state,
        bad_boxes,
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["calibration"] == "INVALID"
    assert decision["reason"] == "CALIBRATION_MISSING_SELL_CONTROL"


def test_shooter_refuses_amount_control_calibration() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    boxes = dict(_boxes())
    boxes["amount_box"] = {"x": 0.9, "y": 0.3}
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(packet_id="amount-box", frame_id=2, capture_count=2, state_version=2),
        state,
        boxes,
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["calibration"] == "INVALID"
    assert decision["reason"] == "CALIBRATION_AMOUNT_CONTROL_FORBIDDEN"


def test_shooter_does_not_change_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    calls: list[str] = []
    rect = shooter.RECT()
    rect.left = 0
    rect.top = 0
    rect.right = 100
    rect.bottom = 100

    def fail_set_amount(*_args: Any, **_kwargs: Any) -> None:
        calls.append("set_amount")
        raise AssertionError("set_amount must not be called")

    def _activate_window(_hwnd: int) -> bool:
        return True

    def _validate_calibration(_boxes_arg: dict[str, Any], _rect_arg: Any) -> bool:
        return True

    def _is_broker_ready(_hwnd: int, _rect_arg: Any) -> bool:
        return True

    def _resolve_and_set_expiry(
        _hwnd: int,
        _boxes_arg: dict[str, Any],
        _expiry: int,
        _caps: dict[str, Any],
    ) -> bool:
        return True

    def _click_trade_button(_hwnd: int, _boxes_arg: dict[str, Any], side: str) -> None:
        calls.append(f"click:{side}")

    monkeypatch.setattr(shooter, "set_amount", fail_set_amount)
    monkeypatch.setattr(shooter, "activate_window", _activate_window)
    monkeypatch.setattr(shooter, "get_window_rect", _window_rect_reader(rect))
    monkeypatch.setattr(shooter, "validate_calibration", _validate_calibration)
    monkeypatch.setattr(shooter, "is_broker_ready", _is_broker_ready)
    monkeypatch.setattr(shooter, "resolve_and_set_expiry", _resolve_and_set_expiry)
    monkeypatch.setattr(shooter, "click_trade_button", _click_trade_button)

    assert shooter.execute_trade(1, _boxes(), "BUY", 300, 5000) is True
    assert calls == ["click:BUY"]


def test_shooter_does_not_fire_duplicate_packet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="dup", frame_id=2, capture_count=2, state_version=2)
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is True
    shooter._v3_record_execution(state, packet, now=1000.3)

    duplicate_decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.4,
    )
    assert duplicate_decision["will_click"] is False
    assert duplicate_decision["reason"] == "DUPLICATE_PACKET_NOT_REFIRED"


def test_shooter_blocks_new_packets_until_recorded_expiry_clears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    state: dict[str, Any] = {}
    packet = _packet(packet_id="hf-done", frame_id=2, capture_count=2, state_version=2)
    packet["execution"]["expiry_seconds"] = 600
    packet["execution"]["time_sequence"]["target_seconds"] = 600
    packet["execution"]["time_sequence"]["target_text"] = "00:10:00"
    for step in packet["execution"]["time_sequence"]["steps"]:
        if step.get("action") == "type_time":
            step["value"] = "00:10:00"

    shooter._v3_record_execution(state, packet, now=1000.0)

    ok, reason, remaining = shooter._v3_gate2_trade_discipline(state, now=1001.0)
    assert ok is False
    assert reason == "TRADE_DISCIPLINE_ACTIVE_TRADE_UNTIL_EXPIRY"
    assert remaining == 599

    ok_after, reason_after, remaining_after = shooter._v3_gate2_trade_discipline(state, now=1600.1)
    assert ok_after is True
    assert reason_after == "TRADE_DISCIPLINE_PASS"
    assert remaining_after == 0


def test_shooter_never_fires_buy_and_sell() -> None:
    shooter = _load_shooter()
    state: dict[str, Any] = {}
    _prime_second_read(shooter, state)
    packet = _packet(packet_id="both-sides", frame_id=2, capture_count=2, state_version=2)
    packet["execution"]["buy_executable"] = True
    packet["execution"]["sell_executable"] = True
    decision = shooter._evaluate_v3_shooter_decision(
        packet,
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=1000.2,
    )
    assert decision["will_click"] is False
    assert decision["reason"] == "MODEL_COUNCIL_CONFLICT_BOTH_SIDES"


def test_five_trades_triggers_twenty_minute_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    monkeypatch.setattr(shooter, "THREE_GATE_STATE_FILE", tmp_path / "state.json")
    state: dict[str, Any] = {}
    now = 1000.0
    for idx in range(5):
        shooter._v3_record_execution(
            state,
            _packet(packet_id=f"done_{idx}", frame_id=idx + 1, capture_count=idx + 1, state_version=idx + 1),
            now=now,
        )
    assert state["v3_trade_count"] == 0
    assert state["v3_locked_until"] == now + 20 * 60

    state["v3_second_live_read_baseline"] = {
        "session_id": "pocket-live-8788",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": 5,
        "capture_count": 5,
        "state_version": 5,
        "packet_id": "baseline",
        "side": "BUY",
        "input_frame_hash": "hash_5",
        "seen_at": now,
    }
    decision = shooter._evaluate_v3_shooter_decision(
        _packet(packet_id="next", frame_id=6, capture_count=6, state_version=6),
        state,
        _boxes(),
        expected_session_id="pocket-live-8788",
        now=now + 1,
    )
    assert decision["will_click"] is False
    assert decision["gate_2_trade_discipline"] == "LOCKED"
    assert decision["discipline_remaining_seconds"] == 20 * 60 - 1


def test_pre_click_mismatch_blocks_click() -> None:
    shooter = _load_shooter()
    original = _packet(packet_id="ready", frame_id=2, capture_count=2, state_version=2)
    latest = _packet(packet_id="opposite", side="SELL", frame_id=3, capture_count=3, state_version=3)
    ok, reason = shooter._v3_pre_click_confirmation(
        original,
        latest,
        expected_session_id="pocket-live-8788",
        now=1000.5,
    )
    assert ok is False
    assert reason == "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"


def test_pre_click_age_check_blocks_click() -> None:
    shooter = _load_shooter()
    original = _packet(packet_id="ready", frame_id=2, capture_count=2, state_version=2, packet_age_ms=100)
    latest = _packet(packet_id="ready", frame_id=3, capture_count=3, state_version=3, packet_age_ms=5000)
    ok, reason = shooter._v3_pre_click_confirmation(
        original,
        latest,
        expected_session_id="pocket-live-8788",
        now=1000.5,
        max_packet_age_seconds=2.0,
    )
    assert ok is False
    assert reason == "PRE_CLICK_PACKET_STALE"


def test_pre_click_same_side_expiry_change_blocks_click() -> None:
    shooter = _load_shooter()
    original = _packet(packet_id="ready", frame_id=2, capture_count=2, state_version=2, packet_age_ms=100)
    latest = _packet(packet_id="ready", frame_id=2, capture_count=2, state_version=2, packet_age_ms=100)
    latest["execution"]["expiry_seconds"] = 600
    latest["execution"]["time_sequence"]["target_seconds"] = 600
    latest["execution"]["time_sequence"]["target_text"] = "00:10:00"

    ok, reason = shooter._v3_pre_click_confirmation(
        original,
        latest,
        expected_session_id="pocket-live-8788",
        now=1000.5,
    )

    assert ok is False
    assert reason == "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"


def test_runtime_integrity_accepts_canonical_epoch_sec_fields() -> None:
    shooter = _load_shooter()
    packet = _packet(packet_id="sec_only", now=1000.0)
    packet["created_epoch_sec"] = packet.pop("created_epoch")
    packet["valid_until_epoch_sec"] = packet.pop("valid_until_epoch")

    ok, reason = shooter._v3_runtime_integrity_check(
        packet,
        expected_session_id="pocket-live-8788",
        now=1000.0,
    )

    assert ok is True
    assert reason == "RUNTIME_INTEGRITY: PASS"


def test_manual_live_click_requires_env_and_explicit_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    shooter = _load_shooter()
    monkeypatch.delenv(shooter.LIVE_BROKER_CLICK_ENV, raising=False)

    def _fail_find_pocket_option_window(_query: str) -> NoReturn:
        pytest.fail("manual mode should not touch the broker window unless explicitly armed")

    monkeypatch.setattr(
        shooter,
        "find_pocket_option_window",
        _fail_find_pocket_option_window,
    )
    args = __import__("types").SimpleNamespace(
        allow_live_click=False,
        window_query="Pocket Option",
        side="sell",
        expiry=300,
        amount=5,
    )

    assert shooter.run_manual(args) == 2
