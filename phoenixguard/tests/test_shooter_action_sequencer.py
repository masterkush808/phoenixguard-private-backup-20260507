from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Mapping

from phoenixguard.execution.shooter_action_sequencer import (
    ActionEvidenceRecorder,
    BrokerTimingProfile,
    ShooterActionSequencerV2,
)
from tools.analyze_shooter_action_trace import analyze_trace


class FakeAdapter:
    def __init__(self) -> None:
        self.actions: list[tuple[str, tuple[Any, ...]]] = []
        self.clicks: list[tuple[int, int]] = []
        self.moves: list[tuple[int, int, int]] = []
        self.keys: list[str] = []
        self.hotkeys: list[tuple[str, ...]] = []
        self.typed: list[str] = []
        self.sleeps: list[int] = []

    def sleep_ms(self, value: int) -> None:
        self.actions.append(("sleep_ms", (int(value),)))
        self.sleeps.append(int(value))

    def move_to_target(self, x: int, y: int, duration_ms: int) -> None:
        self.actions.append(("move_to_target", (int(x), int(y), int(duration_ms))))
        self.moves.append((int(x), int(y), int(duration_ms)))

    def click_target_once(self, x: int, y: int) -> None:
        self.actions.append(("click_target_once", (int(x), int(y))))
        self.clicks.append((int(x), int(y)))

    def double_click_target(self, x: int, y: int) -> None:
        self.actions.append(("double_click_target", (int(x), int(y))))
        self.clicks.append((int(x), int(y)))

    def press_key(self, key: str) -> None:
        self.actions.append(("press_key", (str(key),)))
        self.keys.append(str(key))

    def hotkey(self, *keys: str) -> None:
        self.actions.append(("hotkey", tuple(str(key) for key in keys)))
        self.hotkeys.append(tuple(str(key) for key in keys))

    def type_text_slowly(self, text: str, interval_ms: int) -> None:
        self.actions.append(("type_text_slowly", (str(text), int(interval_ms))))
        self.typed.append(str(text))

    def capture_step_screenshot(self, path: str | Path, rect: Any = None) -> str:
        self.actions.append(("capture_step_screenshot", (str(path), rect)))
        return str(path)


class FailingClickAdapter(FakeAdapter):
    def click_target_once(self, x: int, y: int) -> None:
        super().click_target_once(x, y)
        raise RuntimeError("os input rejected")


def _boxes(**overrides: Mapping[str, float]) -> dict[str, dict[str, float]]:
    boxes: dict[str, dict[str, float]] = {
        "broker_screen": {"x": 0.40, "y": 0.50},
        "time_button": {"x": 0.80, "y": 0.20},
        "hourly_input": {"x": 0.70, "y": 0.28},
        "minute_input": {"x": 0.74, "y": 0.28},
        "second_input": {"x": 0.78, "y": 0.28},
        "hourly_plus": {"x": 0.70, "y": 0.24},
        "minute_plus": {"x": 0.74, "y": 0.24},
        "time_300": {"x": 0.76, "y": 0.36},
        "buy_icon": {"x": 0.90, "y": 0.46},
        "sell_icon": {"x": 0.90, "y": 0.52},
        "final_screen": {"x": 0.50, "y": 0.75},
    }
    for key, value in overrides.items():
        boxes[key] = dict(value)
    return boxes


def _combined_boxes(**overrides: Mapping[str, float]) -> dict[str, dict[str, float]]:
    boxes = _boxes(time_input={"x": 0.82, "y": 0.28})
    for key, value in overrides.items():
        boxes[key] = dict(value)
    return boxes


def _packet(side: str = "BUY", expiry: int = 300) -> dict[str, Any]:
    now = time.time()
    sequence_context = {
        "sequence_id": "seq-exec-test",
        "session_id": "pocket-live-8788",
        "sequence_index": 1,
        "frame_start": 1,
        "frame_end": 64,
        "sequence_length": 64,
        "frames_received": 64,
        "frames_used": 64,
        "candle_count": 64,
        "timeframe": "M5",
        "sequence_signature": "sig-exec-test",
        "sequence_confidence": 0.92,
        "global_direction": side,
        "local_direction": side,
        "current_phase": "PULLBACK_RETEST",
        "progression_score": 0.9,
        "progression": [{"stage": "context_confirmed", "direction": side, "confidence": 0.9}],
        "motifs": ["impulse", "pullback", "retest"],
        "box_history": [{"label": f"H1 {side}", "bbox": [10, 10, 40, 40], "direction": side}],
        "angle_vectors": [[1.0, 0.0]],
        "sniper_zones": [],
        "target_zones": [],
        "invalidation_zones": [],
        "sequence_status": "COMPLETE",
        "frame_range": [1, 64],
        "candle_range": [1, 64],
        "frames_dropped": 0,
        "entry_progression": {"progression_stage": "SNIPER_READY", "maturity_score": 0.9},
        "tracking_summary": {"global_direction": side, "local_direction": side},
        "sequence_history": [{"label": f"H1 {side}", "bbox": [10, 10, 40, 40]}],
    }
    return {
        "packet_id": "exec-test",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "session_id": "pocket-live-8788",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "frame_id": 64,
        "capture_count": 65,
        "state_version": 166,
        "created_epoch_sec": now - 0.1,
        "valid_until_epoch_sec": now + 2.0,
        "created_epoch": now - 0.1,
        "valid_until_epoch": now + 2.0,
        "provenance": {
            "frame_id": 64,
            "capture_count": 65,
            "state_version": 166,
            "sequence_id": sequence_context["sequence_id"],
            "source_lock_id": "source-lock-exec-test",
            "model_health_id": "mh-exec-test",
            "chart_transform_id": "ct-exec-test",
            "created_epoch_ms": int(round((now - 0.1) * 1000.0)),
            "valid_until_epoch_ms": int(round((now + 2.0) * 1000.0)),
        },
        "instrument_context": {
            "identity_state": "IDENTITY_CONFIRMED",
            "display_symbol": "EUR/GBP OTC",
            "ocr_symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "viewport_hash": "viewport-exec-test",
            "broker_surface_hash": "broker-exec-test",
            "confidence": 0.92,
            "paper_safe": True,
            "broker_click_safe": True,
            "session_id": "pocket-live-8788",
        },
        "symbol_context": {
            "display_symbol": "EUR/GBP OTC",
            "canonical_symbol": "EURGBP-OTC",
            "timeframe": "M5",
        },
        "live_integrity": {
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "hash-64",
            "previous_frame_hash": "hash-63",
            "packet_age_ms": 100,
        },
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": side,
            "expiry_seconds": expiry,
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
            "time_sequence": {
                "target_text": "00:05:00",
                "target_seconds": expiry,
                "steps": [
                    {"action": "focus_time_field"},
                    {"action": "type_time", "value": "00:05:00"},
                    {"action": "confirm_time"},
                ],
            },
        },
        "model_council": {
            "final_state": "EXECUTABLE",
            "final_side": side,
            "decision_id": "mc-exec-test",
            "maturity_stage": "EXECUTABLE_PACKET",
            "contributors_are_diagnostic": True,
            "sequence_context": sequence_context,
        },
        "runtime_model_health": {
            "all_required_models_awake": True,
            "council_status": "AWAKE",
        },
    }


def _sequencer(
    tmp_path: Path,
    *,
    boxes: dict[str, Any] | None = None,
    adapter: FakeAdapter | None = None,
    rects: list[tuple[int, int, int, int]] | None = None,
    timing_profile: BrokerTimingProfile | None = None,
    foreground_checks: list[bool] | None = None,
    ensure_foreground: bool = True,
) -> tuple[ShooterActionSequencerV2, FakeAdapter]:
    adapter = adapter or FakeAdapter()
    rect_values = rects or [(0, 0, 1000, 800)]
    calls = {"idx": 0}
    foreground_values = list(foreground_checks) if foreground_checks is not None else None

    def get_rect(_hwnd: int) -> tuple[int, int, int, int]:
        idx = min(calls["idx"], len(rect_values) - 1)
        calls["idx"] += 1
        return rect_values[idx]

    def is_foreground(_hwnd: int) -> bool:
        if foreground_values is None:
            return True
        if not foreground_values:
            return bool(foreground_values[-1]) if foreground_values else False
        return bool(foreground_values.pop(0))

    sequencer = ShooterActionSequencerV2(
        hwnd=1,
        boxes=boxes or _boxes(),
        get_window_rect=get_rect,
        activate_window=lambda _hwnd: True,
        validate_calibration=lambda _boxes_arg, _rect: True,
        is_broker_ready=lambda _hwnd, _rect: True,
        adapter=adapter,  # type: ignore[arg-type]
        ensure_foreground_window=lambda _hwnd: bool(ensure_foreground),
        is_foreground_window=is_foreground,
        timing_profile=timing_profile or BrokerTimingProfile(
            window_activate_wait_ms=1,
            broker_focus_after_click_ms=1,
            move_duration_ms=1,
            post_move_hold_ms=1,
            post_click_min_wait_ms=1,
            time_button_after_click_wait_ms=1,
            input_focus_wait_ms=1,
            select_existing_value_wait_ms=1,
            typing_key_interval_ms=1,
            post_typing_wait_ms=1,
            post_time_confirm_wait_ms=1,
            final_pre_side_click_hold_ms=1,
            post_side_click_capture_delay_ms=1,
            require_expiry_verification=False,
        ),
        evidence_recorder=ActionEvidenceRecorder(tmp_path, enabled=False, packet_id="exec-test"),
        ocr_reader=None,
    )
    sequencer._test_rect_calls = calls  # type: ignore[attr-defined]
    return sequencer, adapter


def test_type_first_time_path_and_side_click_after_time(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path)
    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "PASS"
    assert result.method == "typed_input"
    assert result.expiry_status == "REASONABLY_CONFIRMED"
    assert adapter.typed == ["05"]
    step_names = [step.step for step in result.steps]
    assert step_names.index("type_minute_value") < step_names.index("final_side_click")
    assert step_names.index("final_pre_side_click_hold") < step_names.index("final_side_click")
    assert result.steps[-1].target == "buy_icon"


def test_combined_time_input_preferred_when_editable_time_field_exists(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path, boxes=_combined_boxes())
    packet = _packet("SELL", 6300)
    packet["execution"]["time_sequence"]["target_text"] = "01:45:00"
    result = sequencer.execute(packet)

    assert result.overall == "PASS"
    assert result.method == "combined_time_input"
    assert adapter.typed == ["01:45:00"]
    step_names = [step.step for step in result.steps]
    assert "focus_combined_time_input" in step_names
    assert step_names.index("type_combined_time_value") < step_names.index("final_side_click")
    assert result.steps[-1].target == "sell_icon"


def test_combined_time_input_fallback_when_split_controls_missing(tmp_path: Path) -> None:
    boxes = _combined_boxes()
    boxes.pop("hourly_input")
    boxes.pop("minute_input")
    boxes.pop("second_input")
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)
    packet = _packet("SELL", 6300)
    packet["execution"]["time_sequence"]["target_text"] = "01:45:00"
    result = sequencer.execute(packet)

    assert result.overall == "PASS"
    assert result.method == "combined_time_input"
    assert adapter.typed == ["01:45:00"]
    assert "focus_combined_time_input" in [step.step for step in result.steps]


def test_time_panel_opens_from_middle_time_input_when_available(tmp_path: Path) -> None:
    sequencer, _adapter = _sequencer(tmp_path, boxes=_combined_boxes())
    result = sequencer.execute(_packet("BUY", 300))

    open_step = next(step for step in result.steps if step.step == "focus_combined_time_input")
    assert open_step.target == "time_input"


def test_calibrated_click_failure_aborts_without_runtime_escape(tmp_path: Path) -> None:
    sequencer, _adapter = _sequencer(tmp_path, adapter=FailingClickAdapter())

    result = sequencer.perform_calibrated_step("open_time_panel", "time_button", expected_region="time_box", wait_after_ms=1)

    assert result.result == "FAILED_ABORT"
    assert "calibrated click failed" in result.reason


def test_time_button_must_be_in_right_order_panel_time_region(tmp_path: Path) -> None:
    boxes = _boxes(time_button={"x": 0.90, "y": 0.43})
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)

    result = sequencer.perform_calibrated_step("open_time_panel", "time_button", expected_region="time_box", wait_after_ms=1)

    assert result.result == "FAILED_ABORT"
    assert result.reason.startswith("CALIBRATED_TARGET_REGION_MISMATCH:time_button:expected_time_box")
    assert adapter.clicks == []


def test_split_time_input_must_live_in_opened_time_panel(tmp_path: Path) -> None:
    boxes = _boxes(second_input={"x": 0.88, "y": 0.30})
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)

    result = sequencer.execute(_packet("BUY", 3661))

    assert result.overall == "FAILED"
    assert "second_input" in result.reason
    assert "popup_control_not_left_of_time_field" in result.reason
    assert "final_side_click" not in [step.step for step in result.steps]
    assert (900, 368) not in adapter.clicks


def test_time_input_and_time_button_can_share_middle_box_point(tmp_path: Path) -> None:
    boxes = _combined_boxes(time_button={"x": 0.82, "y": 0.20}, time_input={"x": 0.82, "y": 0.20})
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)
    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "PASS"
    open_step = next(step for step in result.steps if step.step == "focus_combined_time_input")
    assert open_step.target == "time_input"


def test_typed_input_sets_seconds_when_calibrated(tmp_path: Path) -> None:
    boxes = _boxes(second_input={"x": 0.78, "y": 0.28})
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)
    result = sequencer.execute(_packet("BUY", 3661))

    assert result.overall == "PASS"
    assert adapter.typed == ["01", "01", "01"]
    assert ("double_click_target", (780, 224)) in adapter.actions
    step_names = [step.step for step in result.steps]
    assert step_names.index("type_second_value") < step_names.index("confirm_typed_time_enter")


def test_typed_input_accepts_plural_seconds_calibration_alias(tmp_path: Path) -> None:
    boxes = _boxes()
    boxes["seconds_input"] = boxes.pop("second_input")
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)

    result = sequencer.execute(_packet("BUY", 3661))

    assert result.overall == "PASS"
    assert result.method == "typed_input"
    assert adapter.typed == ["01", "01", "01"]
    second_step = next(step for step in result.steps if step.step == "type_second_focus")
    assert second_step.target == "seconds_input"


def test_second_precision_expiry_aborts_without_seconds_capable_control(tmp_path: Path) -> None:
    boxes = _boxes()
    boxes.pop("time_300", None)
    boxes.pop("time_input", None)
    boxes.pop("time_box", None)
    boxes.pop("expiry_time_field", None)
    boxes.pop("second_input")
    sequencer, _adapter = _sequencer(tmp_path, boxes=boxes)
    result = sequencer.execute(_packet("SELL", 61))

    assert result.overall == "FAILED"
    assert "typed split controls missing:second_input" in result.reason
    assert "final_side_click" not in [step.step for step in result.steps]


def test_combined_mismatch_retries_split_time_fields(tmp_path: Path) -> None:
    profile = BrokerTimingProfile(
        window_activate_wait_ms=1,
        broker_focus_after_click_ms=1,
        move_duration_ms=1,
        post_move_hold_ms=1,
        post_click_min_wait_ms=1,
        time_button_after_click_wait_ms=1,
        input_focus_wait_ms=1,
        select_existing_value_wait_ms=1,
        typing_key_interval_ms=1,
        post_typing_wait_ms=1,
        post_time_confirm_wait_ms=1,
        final_pre_side_click_hold_ms=1,
        post_side_click_capture_delay_ms=1,
        require_expiry_verification=True,
    )
    sequencer, adapter = _sequencer(tmp_path, boxes=_combined_boxes(), timing_profile=profile)
    readings = [60, 0, 3661]
    sequencer.ocr_reader = lambda _hwnd, _boxes_arg: readings.pop(0)
    packet = _packet("BUY", 3661)
    packet["execution"]["time_sequence"]["target_text"] = "01:01:01"

    result = sequencer.execute(packet)

    assert result.overall == "PASS"
    assert result.method == "typed_input"
    assert adapter.typed == ["01:01:01", "01", "01", "01"]
    assert "focus_combined_time_input" in [step.step for step in result.steps]
    assert "type_second_focus" in [step.step for step in result.steps]


def test_visible_timer_uses_primary_calibrated_adjustment(tmp_path: Path) -> None:
    profile = BrokerTimingProfile(
        arrow_fallback_enabled=True,
        max_total_arrow_clicks=36,
        wait_between_arrow_clicks_ms=1,
        window_activate_wait_ms=1,
        broker_focus_after_click_ms=1,
        move_duration_ms=1,
        post_move_hold_ms=1,
        post_click_min_wait_ms=1,
        time_button_after_click_wait_ms=1,
        input_focus_wait_ms=1,
        select_existing_value_wait_ms=1,
        typing_key_interval_ms=1,
        post_typing_wait_ms=1,
        post_time_confirm_wait_ms=1,
        final_pre_side_click_hold_ms=1,
        post_side_click_capture_delay_ms=1,
        require_expiry_verification=True,
    )
    boxes = _combined_boxes(
        hourly_minus={"x": 0.70, "y": 0.32},
        minute_minus={"x": 0.74, "y": 0.32},
        second_plus={"x": 0.78, "y": 0.24},
        second_minus={"x": 0.78, "y": 0.32},
    )
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes, timing_profile=profile)
    readings = [14400, 600]
    sequencer.ocr_reader = lambda _hwnd, _boxes_arg: readings.pop(0)
    packet = _packet("BUY", 600)
    packet["execution"]["time_sequence"]["target_text"] = "00:10:00"

    result = sequencer.execute(packet)

    step_names = [step.step for step in result.steps]
    assert result.overall == "PASS"
    assert result.method == "calibrated_control_adjustment"
    assert result.expiry_status == "VERIFIED_TEXT"
    assert adapter.typed == []
    assert "arrow_hour_minus_1" in step_names
    assert "arrow_hour_minus_4" in step_names
    assert "arrow_minute_plus_10" in step_names
    assert "final_side_click" in step_names


def test_combined_time_ocr_no_read_uses_completed_calibrated_control_path(tmp_path: Path) -> None:
    profile = BrokerTimingProfile(
        window_activate_wait_ms=1,
        broker_focus_after_click_ms=1,
        move_duration_ms=1,
        post_move_hold_ms=1,
        post_click_min_wait_ms=1,
        time_button_after_click_wait_ms=1,
        input_focus_wait_ms=1,
        select_existing_value_wait_ms=1,
        typing_key_interval_ms=1,
        post_typing_wait_ms=1,
        post_time_confirm_wait_ms=1,
        final_pre_side_click_hold_ms=1,
        post_side_click_capture_delay_ms=1,
        require_expiry_verification=True,
    )
    sequencer, adapter = _sequencer(tmp_path, boxes=_combined_boxes(), timing_profile=profile)
    sequencer.ocr_reader = lambda _hwnd, _boxes_arg: None
    packet = _packet("BUY", 600)
    packet["execution"]["time_sequence"]["target_text"] = "00:10:00"

    result = sequencer.execute(packet)

    step_names = [step.step for step in result.steps]
    assert result.overall == "PASS"
    assert result.method == "combined_time_input"
    assert result.expiry_status == "CALIBRATED_CONTROL_CONFIRMED"
    assert adapter.typed == ["00:10:00"]
    assert "type_hour_focus" not in step_names
    assert "type_minute_focus" not in step_names
    assert "type_second_focus" not in step_names
    assert "final_side_click" in step_names


def test_action_aborts_if_target_window_not_foreground(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path, foreground_checks=[True, False, False], ensure_foreground=False)
    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "FAILED"
    assert result.reason == "TARGET_WINDOW_NOT_FOREGROUND"
    assert adapter.clicks == []


def test_time_only_validation_skips_side_click_after_final_hold(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path)
    result = sequencer.execute(_packet("SELL", 300), skip_side_click=True)

    assert result.overall == "PASS_TIME_ONLY"
    assert result.state == "SIDE_CLICK_SKIPPED"
    assert [step.step for step in result.steps][-1] == "final_pre_side_click_hold"
    assert all(step.target != "sell_icon" for step in result.steps)
    assert adapter.clicks
    assert adapter.clicks[-1] != (900, 416)


def test_coordinate_resolved_fresh_each_step(tmp_path: Path) -> None:
    rects = [
        (0, 0, 1000, 800),
        (5, 0, 1005, 800),
        (10, 0, 1010, 800),
        (15, 0, 1015, 800),
        (20, 0, 1020, 800),
        (25, 0, 1025, 800),
        (30, 0, 1030, 800),
        (35, 0, 1035, 800),
        (40, 0, 1040, 800),
        (45, 0, 1045, 800),
        (50, 0, 1050, 800),
    ]
    sequencer, _adapter = _sequencer(tmp_path, rects=rects)
    result = sequencer.execute(_packet("SELL", 300))

    click_steps = [step for step in result.steps if step.coordinate_abs]
    assert len({tuple(step.window_rect or ()) for step in click_steps}) > 1
    assert click_steps[0].coordinate_abs != click_steps[-1].coordinate_abs
    assert result.steps[-1].target == "sell_icon"


def test_exact_preset_fallback_when_typed_controls_missing(tmp_path: Path) -> None:
    boxes = _boxes()
    boxes.pop("hourly_input")
    boxes.pop("minute_input")
    boxes.pop("second_input")
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)
    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "PASS"
    assert result.method == "exact_preset"
    assert "select_exact_preset" in [step.step for step in result.steps]
    assert adapter.typed == []


def test_manifest_authoritative_boxes_use_combined_time_without_legacy_split_targets(tmp_path: Path) -> None:
    boxes = {
        "broker_screen": {"x": 0.75, "y": 0.29, "calibration_source": "user_calibration_manifest"},
        "time_button": {"x": 0.89, "y": 0.26, "calibration_source": "user_calibration_manifest"},
        "time_input": {"x": 0.89, "y": 0.26, "calibration_source": "user_calibration_manifest"},
        "expiry_time_field": {"x": 0.89, "y": 0.26, "calibration_source": "user_calibration_manifest"},
        "hourly_plus": {"x": 0.77, "y": 0.26, "calibration_source": "user_calibration_manifest"},
        "hourly_minus": {"x": 0.78, "y": 0.32, "calibration_source": "user_calibration_manifest"},
        "buy_icon": {"x": 0.92, "y": 0.44, "calibration_source": "user_calibration_manifest"},
        "sell_icon": {"x": 0.89, "y": 0.48, "calibration_source": "user_calibration_manifest"},
        "capabilities": {"authoritative_manifest": True, "legacy_box_fallback_allowed": False},
    }
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)
    result = sequencer.execute(_packet("BUY", 600))

    step_names = [step.step for step in result.steps]
    assert result.overall == "PASS"
    assert result.method == "combined_time_input"
    assert "focus_combined_time_input" in step_names
    assert "type_hour_focus" not in step_names
    assert "type_minute_focus" not in step_names
    assert "type_second_focus" not in step_names
    assert "select_exact_preset" not in step_names
    assert len(adapter.typed) == 1
    assert adapter.typed[0].count(":") == 2


def test_arrow_fallback_bounded_aborts_before_side_click(tmp_path: Path) -> None:
    boxes = _boxes()
    for key in ("hourly_input", "minute_input", "second_input", "time_300"):
        boxes.pop(key)
    sequencer, _adapter = _sequencer(
        tmp_path,
        boxes=boxes,
        timing_profile=BrokerTimingProfile(
            arrow_fallback_enabled=True,
            require_expiry_verification=False,
            window_activate_wait_ms=1,
            broker_focus_after_click_ms=1,
            move_duration_ms=1,
            post_move_hold_ms=1,
            post_click_min_wait_ms=1,
            time_button_after_click_wait_ms=1,
            input_focus_wait_ms=1,
            select_existing_value_wait_ms=1,
            typing_key_interval_ms=1,
            post_typing_wait_ms=1,
            post_time_confirm_wait_ms=1,
            final_pre_side_click_hold_ms=1,
            post_side_click_capture_delay_ms=1,
        ),
    )
    sequencer.ocr_reader = lambda _hwnd, _boxes_arg: 0
    result = sequencer.execute(_packet("BUY", 900))

    assert result.overall == "FAILED"
    assert result.state == "ABORT_BEFORE_SIDE_CLICK"
    assert "calibrated adjustment bounded" in result.reason
    assert "final_side_click" not in [step.step for step in result.steps]


def test_trace_analyzer_detects_skipped_time_input() -> None:
    findings = analyze_trace(
        [
            {
                "step": "final_side_click",
                "target": "buy_icon",
                "result": "PASS",
                "window_rect": [0, 0, 1000, 800],
                "wait_after_ms": 900,
            }
        ],
        _boxes(),
    )

    assert "BUY_SELL_CLICKED_BEFORE_EXPIRY_SEQUENCE_COMPLETED" in findings
    assert "TIME_PANEL_OR_TIME_INPUT_STEP_MISSING" in findings


def test_all_live_actions_go_through_low_level_adapter(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path)

    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "PASS"
    assert adapter.actions
    assert {name for name, _args in adapter.actions}.issubset(
        {
            "sleep_ms",
            "move_to_target",
            "click_target_once",
            "double_click_target",
            "press_key",
            "hotkey",
            "type_text_slowly",
            "capture_step_screenshot",
        }
    )
    click_steps = [step for step in result.steps if step.result == "PASS" and step.coordinate_abs]
    assert len(adapter.clicks) == len(click_steps)


def test_coordinate_resolved_fresh_each_action(tmp_path: Path) -> None:
    rects = [
        (0, 0, 1000, 800),
        (10, 0, 1010, 800),
        (20, 0, 1020, 800),
        (30, 0, 1030, 800),
        (40, 0, 1040, 800),
        (50, 0, 1050, 800),
        (60, 0, 1060, 800),
        (70, 0, 1070, 800),
        (80, 0, 1080, 800),
    ]
    sequencer, _adapter = _sequencer(tmp_path, rects=rects)

    result = sequencer.execute(_packet("SELL", 300))

    action_steps = [step for step in result.steps if step.coordinate_abs]
    assert result.overall == "PASS"
    assert len({tuple(step.window_rect or ()) for step in action_steps}) > 1
    assert action_steps[0].coordinate_abs != action_steps[-1].coordinate_abs


def test_window_rect_checked_each_action(tmp_path: Path) -> None:
    sequencer, _adapter = _sequencer(tmp_path)

    result = sequencer.execute(_packet("BUY", 300))

    action_steps = [step for step in result.steps if step.coordinate_abs]
    assert result.overall == "PASS"
    assert all(step.window_rect is not None for step in action_steps)
    assert sequencer._test_rect_calls["idx"] >= len(action_steps) + 2  # type: ignore[attr-defined]


def test_broker_timing_profile_loaded() -> None:
    profile = BrokerTimingProfile.from_file(Path("config") / "shooter_broker_timing_profile.json")

    assert profile.profile_id == "pocket_option_edge_local_v1"
    assert profile.time_button_after_click_wait_ms == 900
    assert profile.final_pre_side_click_hold_ms == 500
    assert profile.require_expiry_verification is True
    assert profile.arrow_fallback_enabled is True
    assert profile.max_total_arrow_clicks == 36


def test_time_typing_primary_path(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path)

    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "PASS"
    assert result.method == "typed_input"
    assert adapter.typed == ["05"]
    assert "final_side_click" in [step.step for step in result.steps]


def test_fast_ui_profile_keeps_live_action_waits_bounded() -> None:
    profile = BrokerTimingProfile().with_speed("fast-ui")

    assert profile.window_activate_wait_ms <= 140
    assert profile.move_duration_ms <= 55
    assert profile.time_button_after_click_wait_ms <= 260
    assert profile.input_focus_wait_ms <= 95
    assert profile.post_typing_wait_ms <= 95
    assert profile.post_time_confirm_wait_ms <= 140
    assert profile.final_pre_side_click_hold_ms <= 35
    assert profile.post_side_click_capture_delay_ms <= 160


def test_exact_preset_fallback(tmp_path: Path) -> None:
    boxes = _boxes()
    boxes.pop("hourly_input")
    boxes.pop("minute_input")
    boxes.pop("second_input")
    sequencer, adapter = _sequencer(tmp_path, boxes=boxes)

    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "PASS"
    assert result.method == "exact_preset"
    assert "select_exact_preset" in [step.step for step in result.steps]
    assert adapter.typed == []


def test_arrow_fallback_bounded(tmp_path: Path) -> None:
    boxes = _boxes()
    for key in ("hourly_input", "minute_input", "second_input", "time_300"):
        boxes.pop(key)
    sequencer, _adapter = _sequencer(
        tmp_path,
        boxes=boxes,
        timing_profile=BrokerTimingProfile(
            arrow_fallback_enabled=True,
            require_expiry_verification=False,
            window_activate_wait_ms=1,
            broker_focus_after_click_ms=1,
            move_duration_ms=1,
            post_move_hold_ms=1,
            post_click_min_wait_ms=1,
            time_button_after_click_wait_ms=1,
            input_focus_wait_ms=1,
            select_existing_value_wait_ms=1,
            typing_key_interval_ms=1,
            post_typing_wait_ms=1,
            post_time_confirm_wait_ms=1,
            final_pre_side_click_hold_ms=1,
            post_side_click_capture_delay_ms=1,
        ),
    )

    sequencer.ocr_reader = lambda _hwnd, _boxes_arg: 0
    result = sequencer.execute(_packet("BUY", 900))

    assert result.overall == "FAILED"
    assert result.state == "ABORT_BEFORE_SIDE_CLICK"
    assert "calibrated adjustment bounded" in result.reason
    assert "final_side_click" not in [step.step for step in result.steps]


def test_arrow_fallback_disabled_by_default_blocks_before_side_click(tmp_path: Path) -> None:
    boxes = _boxes()
    for key in ("hourly_input", "minute_input", "second_input", "time_300"):
        boxes.pop(key)
    sequencer, _adapter = _sequencer(tmp_path, boxes=boxes)

    result = sequencer.execute(_packet("BUY", 900))

    assert result.overall == "FAILED"
    assert result.reason == "typed split controls missing:hourly_input,minute_input,second_input"
    assert "final_side_click" not in [step.step for step in result.steps]


def test_required_expiry_verification_blocks_missing_ocr_before_side_click(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(
        tmp_path,
        timing_profile=BrokerTimingProfile(
            require_expiry_verification=True,
            window_activate_wait_ms=1,
            broker_focus_after_click_ms=1,
            move_duration_ms=1,
            post_move_hold_ms=1,
            post_click_min_wait_ms=1,
            time_button_after_click_wait_ms=1,
            input_focus_wait_ms=1,
            select_existing_value_wait_ms=1,
            typing_key_interval_ms=1,
            post_typing_wait_ms=1,
            post_time_confirm_wait_ms=1,
            final_pre_side_click_hold_ms=1,
            post_side_click_capture_delay_ms=1,
        ),
    )

    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "FAILED"
    assert result.state == "ABORT_BEFORE_SIDE_CLICK"
    assert result.expiry_status == "UNVERIFIED_ABORT"
    assert "final_side_click" not in [step.step for step in result.steps]
    assert (900, 368) not in adapter.clicks


def test_abort_before_side_click_if_time_unconfirmed(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path)
    sequencer.ocr_reader = lambda _hwnd, _boxes_arg: 60

    result = sequencer.execute(_packet("BUY", 300))

    assert result.overall == "FAILED"
    assert result.state == "ABORT_BEFORE_SIDE_CLICK"
    assert result.expiry_status == "UNVERIFIED_ABORT"
    assert "final_side_click" not in [step.step for step in result.steps]
    assert (900, 368) not in adapter.clicks


def test_side_click_once_only(tmp_path: Path) -> None:
    sequencer, adapter = _sequencer(tmp_path)

    result = sequencer.execute(_packet("SELL", 300))

    side_steps = [step for step in result.steps if step.step == "final_side_click"]
    assert result.overall == "PASS"
    assert len(side_steps) == 1
    assert adapter.clicks.count(side_steps[0].coordinate_abs) == 1


def test_action_evidence_written(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    sequencer, _adapter = _sequencer(tmp_path, adapter=adapter)
    sequencer.evidence_recorder = ActionEvidenceRecorder(tmp_path / "evidence", enabled=True, packet_id="exec-test")

    result = sequencer.execute(_packet("BUY", 300))

    trace_path = Path(result.trace_path)
    assert result.overall == "PASS"
    assert trace_path.exists()
    trace_text = trace_path.read_text(encoding="utf-8")
    assert '"packet_id": "exec-test"' in trace_text
    assert '"step": "final_side_click"' in trace_text
    assert any(name == "capture_step_screenshot" for name, _args in adapter.actions)
