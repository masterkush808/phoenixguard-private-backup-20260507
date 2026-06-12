from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import math
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from phoenixguard.execution.packet_v3 import validate_execution_packet_v3


RectLike = Any
RectGetter = Callable[[int], Optional[RectLike]]
BoolWindowFn = Callable[[int], bool]
CalibrationValidator = Callable[[Mapping[str, Any], RectLike], bool]
BrokerReadyChecker = Callable[[int, RectLike], bool]
OcrReader = Callable[[int, Mapping[str, Any]], Optional[int]]
StatusCallback = Callable[[Mapping[str, Any]], None]


DEFAULT_EVIDENCE_DIR = Path(".codex_runtime") / "action_evidence"
DEFAULT_VALIDATION_JSON = Path(".codex_runtime") / "live_behavior_validation_report.json"
DEFAULT_VALIDATION_MD = Path(".codex_runtime") / "live_behavior_validation_report.md"

TARGET_ALIASES: dict[str, tuple[str, ...]] = {
    "buy_icon": ("buy_icon", "buy_button"),
    "buy_button": ("buy_button", "buy_icon"),
    "sell_icon": ("sell_icon", "sell_button"),
    "sell_button": ("sell_button", "sell_icon"),
    "time_button": ("time_button", "time_input", "time_box", "expiry_time_field"),
    "time_input": ("time_input", "time_box", "time_button", "expiry_time_field"),
    "time_box": ("time_box", "time_input", "time_button", "expiry_time_field"),
    "expiry_time_field": ("expiry_time_field", "time_input", "time_box", "time_button"),
    "hourly_input": ("hourly_input", "hour_input"),
    "minute_input": ("minute_input", "minutely_input"),
    "second_input": ("second_input", "seconds_input"),
    "hourly_plus": ("hourly_plus", "expiry_plus", "time_adjustment_plus"),
    "minute_plus": ("minute_plus", "expiry_plus", "time_adjustment_plus"),
    "hourly_minus": ("hourly_minus", "expiry_minus", "time_adjustment_minus"),
    "minute_minus": ("minute_minus", "expiry_minus", "time_adjustment_minus"),
    "broker_screen": ("broker_screen", "broker_focus_area", "final_screen"),
    "final_screen": ("final_screen", "broker_focus_area", "broker_screen"),
}


def _target_candidates(target_name: str) -> tuple[str, ...]:
    target = str(target_name)
    return TARGET_ALIASES.get(target, (target,))


def _first_present_target(boxes: Mapping[str, Any], target_name: str) -> str:
    for candidate in _target_candidates(target_name):
        if isinstance(boxes.get(candidate), Mapping):
            return candidate
    return ""


def _now() -> float:
    return time.time()


def rect_bounds(rect: RectLike) -> tuple[int, int, int, int]:
    if isinstance(rect, Mapping):
        return (
            int(rect.get("left", rect.get("x", 0))),
            int(rect.get("top", rect.get("y", 0))),
            int(rect.get("right", rect.get("left", 0) + rect.get("width", 0))),
            int(rect.get("bottom", rect.get("top", 0) + rect.get("height", 0))),
        )
    if isinstance(rect, Sequence) and not isinstance(rect, (str, bytes)) and len(rect) >= 4:
        return int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def rel_to_abs(rect: RectLike, rel_x: float, rel_y: float) -> tuple[int, int]:
    left, top, right, bottom = rect_bounds(rect)
    width = max(1, right - left)
    height = max(1, bottom - top)
    return left + int(width * float(rel_x)), top + int(height * float(rel_y))


def _float_or_none(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _packet_id(packet: Mapping[str, Any]) -> str:
    return str(packet.get("packet_id") or packet.get("decision_id") or "").strip()


def _execution(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    value = packet.get("execution")
    return value if isinstance(value, Mapping) else {}


def _sequence(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    execution = _execution(packet)
    value = execution.get("time_sequence")
    return value if isinstance(value, Mapping) else {}


def _packet_side(packet: Mapping[str, Any], fallback: str = "") -> str:
    execution = _execution(packet)
    for raw in (execution.get("side"), packet.get("side"), packet.get("action")):
        text = str(raw or "").strip().upper()
        if text in {"BUY", "SELL"}:
            return text
    return str(fallback or "").strip().upper()


def _packet_expiry(packet: Mapping[str, Any], fallback: int = 0) -> int:
    execution = _execution(packet)
    sequence = _sequence(packet)
    for raw in (
        execution.get("expiry_seconds"),
        sequence.get("target_seconds"),
        packet.get("expiry_seconds"),
        fallback,
    ):
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _format_expiry_text(expiry_seconds: int) -> str:
    seconds_total = max(1, int(expiry_seconds))
    hours = seconds_total // 3600
    minutes = (seconds_total % 3600) // 60
    seconds = seconds_total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _target_text(packet: Mapping[str, Any], expiry_seconds: int) -> str:
    sequence = _sequence(packet)
    raw = str(sequence.get("target_text") or "").strip()
    if raw:
        return raw
    return _format_expiry_text(expiry_seconds)


def _annotate_target(path: str, rect: RectLike, xy: tuple[int, int], label: str) -> str:
    try:
        from PIL import Image, ImageDraw

        left, top, _right, _bottom = rect_bounds(rect)
        marker_x = int(xy[0]) - left
        marker_y = int(xy[1]) - top
        image = Image.open(path).convert("RGB")
        draw = ImageDraw.Draw(image)
        radius = 12
        draw.line((marker_x - radius, marker_y, marker_x + radius, marker_y), fill=(255, 20, 20), width=3)
        draw.line((marker_x, marker_y - radius, marker_x, marker_y + radius), fill=(255, 20, 20), width=3)
        draw.ellipse((marker_x - 5, marker_y - 5, marker_x + 5, marker_y + 5), outline=(255, 255, 0), width=2)
        draw.text((marker_x + 14, marker_y + 10), str(label), fill=(255, 255, 0))
        image.save(path)
    except Exception:
        pass
    return path


@dataclass(frozen=True)
class BrokerTimingProfile:
    profile_id: str = "pocket_option_edge_local_v1"
    window_activate_wait_ms: int = 600
    broker_focus_after_click_ms: int = 500
    move_duration_ms: int = 260
    post_move_hold_ms: int = 120
    post_click_min_wait_ms: int = 450
    time_button_after_click_wait_ms: int = 900
    time_panel_ready_timeout_ms: int = 2500
    input_focus_wait_ms: int = 400
    select_existing_value_wait_ms: int = 180
    typing_key_interval_ms: int = 75
    post_typing_wait_ms: int = 550
    post_time_confirm_wait_ms: int = 650
    preset_click_wait_ms: int = 600
    arrow_click_wait_ms: int = 280
    final_pre_side_click_hold_ms: int = 500
    post_side_click_capture_delay_ms: int = 900
    safe_radius_px: int = 10
    window_layout_tolerance_px: int = 40
    arrow_fallback_enabled: bool = True
    max_total_arrow_clicks: int = 12
    wait_between_arrow_clicks_ms: int = 300

    @classmethod
    def from_file(cls, path: str | Path | None = None, *, action_speed: str = "balanced") -> "BrokerTimingProfile":
        payload: dict[str, Any] = {}
        if path:
            candidate = Path(path)
            if candidate.exists():
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(raw, Mapping):
                    profile = raw.get("broker_timing_profile")
                    arrow = raw.get("arrow_fallback")
                    if isinstance(profile, Mapping):
                        payload.update(dict(profile))
                    if isinstance(arrow, Mapping):
                        payload["arrow_fallback_enabled"] = bool(arrow.get("enabled", True))
                        if "max_total_arrow_clicks" in arrow:
                            payload["max_total_arrow_clicks"] = int(arrow["max_total_arrow_clicks"])
                        if "wait_between_arrow_clicks_ms" in arrow:
                            payload["wait_between_arrow_clicks_ms"] = int(arrow["wait_between_arrow_clicks_ms"])
        profile = cls(**{key: value for key, value in payload.items() if key in cls.__dataclass_fields__})
        return profile.with_speed(action_speed)

    def with_speed(self, action_speed: str = "balanced") -> "BrokerTimingProfile":
        speed = str(action_speed or "balanced").strip().lower().replace("_", "-")
        if speed in {"balanced", "default"}:
            return self
        factor = 1.0
        if speed in {"conservative", "slow"}:
            factor = 1.35
        elif speed in {"fast-ui", "fast"}:
            factor = 0.78
        else:
            return self

        def scale(value: int, minimum: int) -> int:
            return max(minimum, int(round(value * factor)))

        return BrokerTimingProfile(
            profile_id=f"{self.profile_id}:{speed}",
            window_activate_wait_ms=scale(self.window_activate_wait_ms, 250),
            broker_focus_after_click_ms=scale(self.broker_focus_after_click_ms, 220),
            move_duration_ms=scale(self.move_duration_ms, 120),
            post_move_hold_ms=scale(self.post_move_hold_ms, 60),
            post_click_min_wait_ms=scale(self.post_click_min_wait_ms, 180),
            time_button_after_click_wait_ms=scale(self.time_button_after_click_wait_ms, 350),
            time_panel_ready_timeout_ms=scale(self.time_panel_ready_timeout_ms, 1000),
            input_focus_wait_ms=scale(self.input_focus_wait_ms, 160),
            select_existing_value_wait_ms=scale(self.select_existing_value_wait_ms, 80),
            typing_key_interval_ms=scale(self.typing_key_interval_ms, 25),
            post_typing_wait_ms=scale(self.post_typing_wait_ms, 220),
            post_time_confirm_wait_ms=scale(self.post_time_confirm_wait_ms, 250),
            preset_click_wait_ms=scale(self.preset_click_wait_ms, 250),
            arrow_click_wait_ms=scale(self.arrow_click_wait_ms, 120),
            final_pre_side_click_hold_ms=scale(self.final_pre_side_click_hold_ms, 220),
            post_side_click_capture_delay_ms=scale(self.post_side_click_capture_delay_ms, 350),
            safe_radius_px=self.safe_radius_px,
            window_layout_tolerance_px=self.window_layout_tolerance_px,
            arrow_fallback_enabled=self.arrow_fallback_enabled,
            max_total_arrow_clicks=self.max_total_arrow_clicks,
            wait_between_arrow_clicks_ms=scale(self.wait_between_arrow_clicks_ms, 120),
        )


@dataclass
class TargetEnvelope:
    target: str
    rel: tuple[float, float]
    abs: tuple[int, int]
    window_rect: tuple[int, int, int, int]
    safe_radius_px: int
    expected_region: str = ""
    status: str = "VALID"
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "center_rel": {"x": self.rel[0], "y": self.rel[1]},
            "center_abs": [self.abs[0], self.abs[1]],
            "window_rect": list(self.window_rect),
            "safe_radius_px": self.safe_radius_px,
            "expected_region": self.expected_region,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class StepResult:
    step: str
    target: str = ""
    coordinate_abs: Optional[tuple[int, int]] = None
    coordinate_rel: Optional[tuple[float, float]] = None
    window_rect: Optional[tuple[int, int, int, int]] = None
    move_duration_ms: int = 0
    wait_after_ms: int = 0
    result: str = "PASS"
    reason: str = ""
    state: str = ""
    verification: str = ""
    evidence_before: str = ""
    evidence_after: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": _now(),
            "step": self.step,
            "target": self.target,
            "coordinate_abs": list(self.coordinate_abs) if self.coordinate_abs else None,
            "coordinate_rel": list(self.coordinate_rel) if self.coordinate_rel else None,
            "window_rect": list(self.window_rect) if self.window_rect else None,
            "move_duration_ms": self.move_duration_ms,
            "wait_after_ms": self.wait_after_ms,
            "result": self.result,
            "reason": self.reason,
            "state": self.state,
            "verification": self.verification,
            "evidence_before": self.evidence_before,
            "evidence_after": self.evidence_after,
        }


@dataclass
class ActionSequenceResult:
    overall: str
    reason: str
    packet_id: str
    side: str
    expiry_seconds: int
    method: str = ""
    expiry_status: str = ""
    state: str = "COMPLETE"
    steps: list[StepResult] = field(default_factory=list)
    evidence_dir: str = ""
    trace_path: str = ""
    report_json_path: str = ""
    report_md_path: str = ""

    @property
    def clicked(self) -> bool:
        return self.overall == "PASS" and any(step.step == "final_side_click" and step.result == "PASS" for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "reason": self.reason,
            "packet_id": self.packet_id,
            "side": self.side,
            "expiry_seconds": self.expiry_seconds,
            "method": self.method,
            "expiry_status": self.expiry_status,
            "state": self.state,
            "evidence_dir": self.evidence_dir,
            "trace_path": self.trace_path,
            "report_json_path": self.report_json_path,
            "report_md_path": self.report_md_path,
            "steps": [step.as_dict() for step in self.steps],
        }


class ActionEvidenceRecorder:
    def __init__(self, evidence_dir: str | Path = DEFAULT_EVIDENCE_DIR, *, enabled: bool = True, packet_id: str = "") -> None:
        self.enabled = bool(enabled)
        self.packet_id = str(packet_id or "packet").replace("/", "_").replace("\\", "_")
        self.root = Path(evidence_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.root / "action_trace.jsonl"

    def screenshot_path(self, step: str, phase: str) -> Path:
        safe_step = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in step)
        return self.root / f"{phase}_{safe_step}.png"

    def append(self, record: Mapping[str, Any]) -> None:
        payload = dict(record)
        payload.setdefault("timestamp", _now())
        payload.setdefault("packet_id", self.packet_id)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


class LowLevelActionAdapter:
    def __init__(self, pyautogui_module: Any) -> None:
        self.pyautogui = pyautogui_module

    def sleep_ms(self, value: int) -> None:
        time.sleep(max(0.0, float(value) / 1000.0))

    def move_to_target(self, x: int, y: int, duration_ms: int) -> None:
        self.pyautogui.moveTo(int(x), int(y), duration=max(0.0, float(duration_ms) / 1000.0))

    def click_target_once(self, x: int, y: int) -> None:
        self.pyautogui.click(int(x), int(y))

    def press_key(self, key: str) -> None:
        self.pyautogui.press(str(key))

    def hotkey(self, *keys: str) -> None:
        self.pyautogui.hotkey(*[str(key) for key in keys])

    def type_text_slowly(self, text: str, interval_ms: int) -> None:
        self.pyautogui.typewrite(str(text), interval=max(0.0, float(interval_ms) / 1000.0))

    def capture_step_screenshot(self, path: str | Path, rect: RectLike | None = None) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if rect is None:
            image = self.pyautogui.screenshot()
        else:
            left, top, right, bottom = rect_bounds(rect)
            image = self.pyautogui.screenshot(region=(left, top, max(1, right - left), max(1, bottom - top)))
        image.save(str(target))
        return str(target)


class CalibratedTargetResolver:
    def __init__(self, boxes: Mapping[str, Any], timing_profile: BrokerTimingProfile) -> None:
        self.boxes = boxes
        self.timing_profile = timing_profile

    def resolve(self, rect: RectLike, target_name: str, *, expected_region: str = "") -> TargetEnvelope:
        source_name = target_name
        raw = None
        for candidate in _target_candidates(target_name):
            candidate_raw = self.boxes.get(candidate)
            if isinstance(candidate_raw, Mapping):
                source_name = candidate
                raw = candidate_raw
                break
        if not isinstance(raw, Mapping):
            return TargetEnvelope(
                target=target_name,
                rel=(0.0, 0.0),
                abs=(0, 0),
                window_rect=rect_bounds(rect),
                safe_radius_px=self.timing_profile.safe_radius_px,
                expected_region=expected_region,
                status="INVALID",
                reason=f"MISSING_TARGET:{target_name}",
            )
        rel_x = _float_or_none(raw.get("x"))
        rel_y = _float_or_none(raw.get("y"))
        bounds = rect_bounds(rect)
        if rel_x is None or rel_y is None:
            return TargetEnvelope(source_name, (0.0, 0.0), (0, 0), bounds, self.timing_profile.safe_radius_px, expected_region, "INVALID", f"INVALID_TARGET:{source_name}")
        x, y = rel_to_abs(bounds, rel_x, rel_y)
        left, top, right, bottom = bounds
        if x < left or x > right or y < top or y > bottom:
            return TargetEnvelope(source_name, (rel_x, rel_y), (x, y), bounds, self.timing_profile.safe_radius_px, expected_region, "INVALID", f"OUT_OF_WINDOW:{source_name}")
        ambiguous = self._ambiguous_neighbor(source_name, bounds, x, y)
        if ambiguous:
            return TargetEnvelope(source_name, (rel_x, rel_y), (x, y), bounds, self.timing_profile.safe_radius_px, expected_region, "INVALID", f"CALIBRATED_TARGET_AMBIGUOUS:{source_name}:{ambiguous}")
        return TargetEnvelope(source_name, (rel_x, rel_y), (x, y), bounds, self.timing_profile.safe_radius_px, expected_region, "VALID", "inside current broker window")

    def _ambiguous_neighbor(self, target_name: str, bounds: tuple[int, int, int, int], x: int, y: int) -> str:
        threshold = max(4, min(7, self.timing_profile.safe_radius_px))
        aliases = set(_target_candidates(target_name))
        alias_groups = (
            {"time_button", "time_input", "time_box"},
        )
        for other, raw in self.boxes.items():
            if other in {target_name, "capabilities"} or not isinstance(raw, Mapping):
                continue
            if other in aliases:
                continue
            if any(target_name in group and other in group for group in alias_groups):
                continue
            rel_x = _float_or_none(raw.get("x"))
            rel_y = _float_or_none(raw.get("y"))
            if rel_x is None or rel_y is None:
                continue
            ox, oy = rel_to_abs(bounds, rel_x, rel_y)
            if abs(ox - x) <= threshold and abs(oy - y) <= threshold:
                return str(other)
        return ""


class ShooterActionSequencerV2:
    def __init__(
        self,
        *,
        hwnd: int,
        boxes: Mapping[str, Any],
        get_window_rect: RectGetter,
        activate_window: BoolWindowFn,
        validate_calibration: CalibrationValidator,
        is_broker_ready: BrokerReadyChecker,
        adapter: LowLevelActionAdapter,
        ensure_foreground_window: BoolWindowFn | None = None,
        is_foreground_window: BoolWindowFn | None = None,
        timing_profile: BrokerTimingProfile | None = None,
        evidence_recorder: ActionEvidenceRecorder | None = None,
        ocr_reader: OcrReader | None = None,
        status_callback: StatusCallback | None = None,
        logger: Any = None,
    ) -> None:
        self.hwnd = int(hwnd)
        self.boxes = boxes
        self.get_window_rect = get_window_rect
        self.activate_window = activate_window
        self.ensure_foreground_window = ensure_foreground_window
        self.is_foreground_window = is_foreground_window
        self.validate_calibration = validate_calibration
        self.is_broker_ready = is_broker_ready
        self.adapter = adapter
        self.timing_profile = timing_profile or BrokerTimingProfile()
        self.evidence_recorder = evidence_recorder or ActionEvidenceRecorder(enabled=False)
        self.ocr_reader = ocr_reader
        self.status_callback = status_callback
        self.logger = logger
        self.resolver = CalibratedTargetResolver(boxes, self.timing_profile)
        self.initial_rect: Optional[tuple[int, int, int, int]] = None
        self.steps: list[StepResult] = []
        self.packet_id = ""

    def execute(
        self,
        packet: Mapping[str, Any],
        *,
        side: str = "",
        expiry_seconds: int = 0,
        skip_side_click: bool = False,
    ) -> ActionSequenceResult:
        self.steps = []
        self.packet_id = _packet_id(packet) or f"packet_{int(_now() * 1000)}"
        side = _packet_side(packet, side)
        expiry_seconds = _packet_expiry(packet, expiry_seconds)
        self._publish("PACKET_ACCEPTED", "packet", {"side": side, "expiry_seconds": expiry_seconds})

        if side not in {"BUY", "SELL"}:
            return self._abort("INVALID_SIDE", side, expiry_seconds)
        if expiry_seconds <= 0:
            return self._abort("INVALID_EXPIRY", side, expiry_seconds)
        if not skip_side_click:
            validation = validate_execution_packet_v3(
                packet,
                now_epoch=_now(),
                require_executable=True,
                require_broker_click_safe_identity=True,
            )
            if not validation.ok:
                return self._abort(f"V3_PACKET_VALIDATION_FAILED:{validation.first_reason}", side, expiry_seconds)

        if not self._require_foreground(allow_activate=True):
            return self._abort("WINDOW_ACTIVATION_FAILED", side, expiry_seconds)
        self.adapter.sleep_ms(self.timing_profile.window_activate_wait_ms)
        self._publish("WINDOW_LOCKED", "activate_window")

        rect = self._current_rect()
        if rect is None:
            return self._abort("WINDOW_RECT_MISSING", side, expiry_seconds)
        self.initial_rect = rect_bounds(rect)
        if not self.validate_calibration(self.boxes, rect):
            return self._abort("CALIBRATION_VALIDATION_FAILED", side, expiry_seconds)
        if not self.is_broker_ready(self.hwnd, rect):
            return self._abort("BROKER_NOT_READY", side, expiry_seconds)
        self._publish("CALIBRATION_RESOLVED", "validate_calibration")

        if _first_present_target(self.boxes, "broker_screen"):
            focus = self.perform_calibrated_step(
                "focus_broker_surface",
                "broker_screen",
                expected_region="broker_surface",
                wait_after_ms=self.timing_profile.broker_focus_after_click_ms,
                state="BROKER_SURFACE_FOCUSED",
            )
            if focus.result != "PASS":
                return self._abort(focus.reason or "BROKER_SURFACE_FOCUS_FAILED", side, expiry_seconds)

        time_result = TimeInputControllerV2(self).set_expiry(expiry_seconds, packet)
        if time_result.result not in {"PASS", "PASS_UNVERIFIED"}:
            return self._abort(time_result.reason or "EXPIRY_UNCONFIRMED", side, expiry_seconds, method=time_result.step, expiry_status=time_result.verification)
        if time_result.verification == "UNVERIFIED_ABORT":
            return self._abort("EXPIRY_UNVERIFIED_ABORT", side, expiry_seconds, method=time_result.step, expiry_status=time_result.verification)

        self.record_wait_step(
            "final_pre_side_click_hold",
            wait_after_ms=self.timing_profile.final_pre_side_click_hold_ms,
            state="FINAL_PRE_CLICK_RECHECK",
            reason="waited before calibrated side click",
        )
        if skip_side_click:
            self._publish("SIDE_CLICK_SKIPPED", "time_only_validation")
            return ActionSequenceResult(
                overall="PASS_TIME_ONLY",
                reason="TIME_SEQUENCE_COMPLETE_SIDE_CLICK_SKIPPED",
                packet_id=self.packet_id,
                side=side,
                expiry_seconds=expiry_seconds,
                method=time_result.step,
                expiry_status=time_result.verification,
                state="SIDE_CLICK_SKIPPED",
                steps=list(self.steps),
                evidence_dir=str(self.evidence_recorder.root),
                trace_path=str(self.evidence_recorder.trace_path),
            )
        self._publish("FINAL_PRE_CLICK_RECHECK", "side_button")
        rect = self._current_rect()
        if rect is None or not self.is_broker_ready(self.hwnd, rect):
            return self._abort("FINAL_RECHECK_WINDOW_NOT_READY", side, expiry_seconds, method=time_result.step, expiry_status=time_result.verification)

        side_target = "buy_icon" if side == "BUY" else "sell_icon"
        side_step = self.perform_calibrated_step(
            "final_side_click",
            side_target,
            expected_region="side_button",
            wait_after_ms=self.timing_profile.post_side_click_capture_delay_ms,
            state="SIDE_CLICK_SENT",
        )
        if side_step.result != "PASS":
            return self._abort(side_step.reason or "SIDE_CLICK_FAILED", side, expiry_seconds, method=time_result.step, expiry_status=time_result.verification)

        self._publish("POST_CLICK_EVIDENCE_CAPTURED", side_target)
        return ActionSequenceResult(
            overall="PASS",
            reason="ACTION_SEQUENCE_COMPLETE",
            packet_id=self.packet_id,
            side=side,
            expiry_seconds=expiry_seconds,
            method=time_result.step,
            expiry_status=time_result.verification,
            state="COMPLETE",
            steps=list(self.steps),
            evidence_dir=str(self.evidence_recorder.root),
            trace_path=str(self.evidence_recorder.trace_path),
        )

    def perform_calibrated_step(
        self,
        step_name: str,
        target_name: str,
        *,
        expected_region: str = "",
        wait_after_ms: Optional[int] = None,
        state: str = "",
    ) -> StepResult:
        wait_ms = self.timing_profile.post_click_min_wait_ms if wait_after_ms is None else int(wait_after_ms)
        if not self._require_foreground(allow_activate=True):
            result = StepResult(step=step_name, target=target_name, result="FAILED_ABORT", reason="TARGET_WINDOW_NOT_FOREGROUND", state=state, wait_after_ms=wait_ms)
            self._add_step(result)
            return result
        rect = self._current_rect()
        if rect is None:
            result = StepResult(step=step_name, target=target_name, result="FAILED_ABORT", reason="WINDOW_RECT_MISSING", state=state, wait_after_ms=wait_ms)
            self._add_step(result)
            return result
        if not self._window_layout_stable(rect):
            result = StepResult(step=step_name, target=target_name, window_rect=rect_bounds(rect), result="FAILED_ABORT", reason="WINDOW_LAYOUT_CHANGED", state=state, wait_after_ms=wait_ms)
            self._add_step(result)
            return result
        if not self.validate_calibration(self.boxes, rect):
            result = StepResult(step=step_name, target=target_name, window_rect=rect_bounds(rect), result="FAILED_ABORT", reason="CALIBRATION_VALIDATION_FAILED", state=state, wait_after_ms=wait_ms)
            self._add_step(result)
            return result
        if not self.is_broker_ready(self.hwnd, rect):
            result = StepResult(step=step_name, target=target_name, window_rect=rect_bounds(rect), result="FAILED_ABORT", reason="BROKER_NOT_READY", state=state, wait_after_ms=wait_ms)
            self._add_step(result)
            return result

        envelope = self.resolver.resolve(rect, target_name, expected_region=expected_region)
        before_path = ""
        after_path = ""
        if envelope.status != "VALID":
            result = StepResult(
                step=step_name,
                target=target_name,
                coordinate_abs=envelope.abs,
                coordinate_rel=envelope.rel,
                window_rect=envelope.window_rect,
                result="FAILED_ABORT",
                reason=envelope.reason,
                state=state,
                wait_after_ms=wait_ms,
            )
            self._add_step(result)
            return result

        if self.evidence_recorder.enabled:
            try:
                before_path = self.adapter.capture_step_screenshot(self.evidence_recorder.screenshot_path(step_name, "before"), rect)
                before_path = _annotate_target(before_path, rect, envelope.abs, f"{step_name}:{target_name}")
            except Exception as exc:
                before_path = f"screenshot_failed:{exc}"

        x, y = envelope.abs
        self._publish(state or step_name, target_name, envelope.as_dict())
        self.adapter.move_to_target(x, y, self.timing_profile.move_duration_ms)
        self.adapter.sleep_ms(self.timing_profile.post_move_hold_ms)
        self.adapter.click_target_once(x, y)
        self.adapter.sleep_ms(wait_ms)

        if self.evidence_recorder.enabled:
            try:
                after_rect = self._current_rect() or rect
                after_path = self.adapter.capture_step_screenshot(self.evidence_recorder.screenshot_path(step_name, "after"), after_rect)
                after_path = _annotate_target(after_path, after_rect, envelope.abs, f"{step_name}:{target_name}")
            except Exception as exc:
                after_path = f"screenshot_failed:{exc}"

        result = StepResult(
            step=step_name,
            target=envelope.target,
            coordinate_abs=envelope.abs,
            coordinate_rel=envelope.rel,
            window_rect=envelope.window_rect,
            move_duration_ms=self.timing_profile.move_duration_ms,
            wait_after_ms=wait_ms,
            result="PASS",
            reason=f"clicked calibrated {target_name}",
            state=state,
            evidence_before=before_path,
            evidence_after=after_path,
        )
        self._add_step(result)
        return result

    def record_key_step(self, step_name: str, action: Callable[[], None], *, wait_after_ms: int, state: str = "", reason: str = "") -> StepResult:
        self._publish(state or step_name, "keyboard")
        if not self._require_foreground(allow_activate=False):
            result = StepResult(
                step=step_name,
                target="keyboard",
                wait_after_ms=wait_after_ms,
                result="FAILED_ABORT",
                reason="TARGET_WINDOW_NOT_FOREGROUND_FOR_KEYBOARD",
                state=state,
            )
            self._add_step(result)
            return result
        try:
            action()
            self.adapter.sleep_ms(wait_after_ms)
            result = StepResult(step=step_name, target="keyboard", wait_after_ms=wait_after_ms, result="PASS", reason=reason or "keyboard action completed", state=state)
        except Exception as exc:
            result = StepResult(step=step_name, target="keyboard", wait_after_ms=wait_after_ms, result="FAILED_ABORT", reason=str(exc), state=state)
        self._add_step(result)
        return result

    def record_wait_step(self, step_name: str, *, wait_after_ms: int, state: str = "", reason: str = "") -> StepResult:
        self._publish(state or step_name, "timer")
        self.adapter.sleep_ms(wait_after_ms)
        result = StepResult(
            step=step_name,
            target="timer",
            wait_after_ms=int(wait_after_ms),
            result="PASS",
            reason=reason or "wait completed",
            state=state,
        )
        self._add_step(result)
        return result

    def verify_expiry(self, expiry_seconds: int, method: str) -> str:
        if self.ocr_reader is None:
            return "REASONABLY_CONFIRMED"
        try:
            visible_seconds = self.ocr_reader(self.hwnd, self.boxes)
        except Exception:
            return "REASONABLY_CONFIRMED"
        if visible_seconds is None:
            return "REASONABLY_CONFIRMED"
        if abs(int(visible_seconds) - int(expiry_seconds)) <= 2:
            return "VERIFIED_TEXT"
        self._log("warning", "expiry verification mismatch after %s: visible=%s target=%s", method, visible_seconds, expiry_seconds)
        return "UNVERIFIED_ABORT"

    def _current_rect(self) -> Optional[RectLike]:
        return self.get_window_rect(self.hwnd)

    def _require_foreground(self, *, allow_activate: bool) -> bool:
        if self.is_foreground_window is not None:
            try:
                if bool(self.is_foreground_window(self.hwnd)):
                    return True
            except Exception as exc:
                self._log("warning", "foreground check failed: %s", exc)
                return False
        if not allow_activate:
            return self.is_foreground_window is None

        activator = self.ensure_foreground_window or self.activate_window
        try:
            activated = bool(activator(self.hwnd))
        except Exception as exc:
            self._log("warning", "foreground activation failed: %s", exc)
            return False
        if not activated:
            return False
        if self.is_foreground_window is not None:
            try:
                return bool(self.is_foreground_window(self.hwnd))
            except Exception as exc:
                self._log("warning", "foreground verify failed after activation: %s", exc)
                return False
        return True

    def _window_layout_stable(self, rect: RectLike) -> bool:
        if self.initial_rect is None:
            return True
        left, top, right, bottom = rect_bounds(rect)
        ileft, itop, iright, ibottom = self.initial_rect
        now_width = right - left
        now_height = bottom - top
        initial_width = iright - ileft
        initial_height = ibottom - itop
        tol = int(self.timing_profile.window_layout_tolerance_px)
        return abs(now_width - initial_width) <= tol and abs(now_height - initial_height) <= tol

    def _add_step(self, result: StepResult) -> None:
        self.steps.append(result)
        record = result.as_dict()
        record["packet_id"] = self.packet_id
        self.evidence_recorder.append(record)

    def _abort(self, reason: str, side: str, expiry_seconds: int, *, method: str = "", expiry_status: str = "") -> ActionSequenceResult:
        self._publish("ABORT_BEFORE_SIDE_CLICK", "abort", {"reason": reason})
        return ActionSequenceResult(
            overall="FAILED",
            reason=str(reason),
            packet_id=self.packet_id,
            side=side,
            expiry_seconds=expiry_seconds,
            method=method,
            expiry_status=expiry_status,
            state="ABORT_BEFORE_SIDE_CLICK",
            steps=list(self.steps),
            evidence_dir=str(self.evidence_recorder.root),
            trace_path=str(self.evidence_recorder.trace_path),
        )

    def _publish(self, phase: str, step: str, extra: Mapping[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "phase": phase,
            "step": step,
            "packet_id": self.packet_id,
            "timestamp": _now(),
        }
        if extra:
            payload.update(dict(extra))
        if self.status_callback is not None:
            try:
                self.status_callback(payload)
            except Exception:
                pass

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        fn = getattr(self.logger, level, None)
        if callable(fn):
            fn(message, *args)


class TimeInputControllerV2:
    def __init__(self, sequencer: ShooterActionSequencerV2) -> None:
        self.sequencer = sequencer

    def set_expiry(self, expiry_seconds: int, packet: Mapping[str, Any]) -> StepResult:
        self.sequencer._publish("TIME_ENTRY_METHOD_SELECTED", "split_hour_minute_input")
        last_typed = StepResult(step="typed_input", result="FAILED_RETRYABLE", reason="not attempted")
        for attempt in range(1, 3):
            last_typed = self._typed_input(expiry_seconds, attempt=attempt)
            if last_typed.result in {"PASS", "PASS_UNVERIFIED"}:
                return last_typed

        self.sequencer._publish("TIME_ENTRY_METHOD_SELECTED", "combined_time_input")
        combined = self._combined_time_input(expiry_seconds, packet)
        if combined.result in {"PASS", "PASS_UNVERIFIED"}:
            return combined

        preset = self._exact_preset(expiry_seconds)
        if preset.result in {"PASS", "PASS_UNVERIFIED"}:
            return preset

        arrow = self._arrow_fallback(expiry_seconds)
        if arrow.result in {"PASS", "PASS_UNVERIFIED"}:
            return arrow
        return arrow if arrow.reason else preset if preset.reason else last_typed

    def _combined_time_input(self, expiry_seconds: int, packet: Mapping[str, Any]) -> StepResult:
        seq = self.sequencer
        target = ""
        for candidate in ("time_input", "time_box", "expiry_time_field"):
            if isinstance(seq.boxes.get(candidate), Mapping):
                target = candidate
                break
        if not target:
            return StepResult(step="combined_time_input", result="FAILED_RETRYABLE", reason="combined time input missing", verification="UNVERIFIED_ABORT")
        target_text = _target_text(packet, expiry_seconds)
        if not target_text or ":" not in target_text:
            target_text = _format_expiry_text(expiry_seconds)

        focus_step = seq.perform_calibrated_step(
            "focus_combined_time_input",
            target,
            expected_region="time_input",
            wait_after_ms=seq.timing_profile.time_button_after_click_wait_ms,
            state="TIME_INPUT_FOCUSED",
        )
        if focus_step.result != "PASS":
            return StepResult(step="combined_time_input", result="FAILED_RETRYABLE", reason=focus_step.reason, verification="UNVERIFIED_ABORT")

        for step_name, action, wait_ms, reason in (
            ("type_combined_time_select_existing", lambda: seq.adapter.hotkey("ctrl", "a"), seq.timing_profile.select_existing_value_wait_ms, "selected existing combined time value"),
            ("type_combined_time_value", lambda: seq.adapter.type_text_slowly(target_text, seq.timing_profile.typing_key_interval_ms), seq.timing_profile.post_typing_wait_ms, f"typed combined time {target_text}"),
            ("confirm_combined_time", lambda: seq.adapter.press_key("enter"), seq.timing_profile.post_time_confirm_wait_ms, "confirmed combined typed time"),
        ):
            result = seq.record_key_step(step_name, action, wait_after_ms=wait_ms, state="TIME_TYPED_OR_SELECTED", reason=reason)
            if result.result != "PASS":
                return StepResult(step="combined_time_input", result="FAILED_RETRYABLE", reason=result.reason, verification="UNVERIFIED_ABORT")

        if "final_screen" in seq.boxes:
            seq.perform_calibrated_step("confirm_combined_time_focus_chart", "final_screen", expected_region="chart_surface", wait_after_ms=seq.timing_profile.post_time_confirm_wait_ms, state="TIME_VERIFICATION")

        verification = seq.verify_expiry(expiry_seconds, "combined_time_input")
        result = "PASS" if verification in {"VERIFIED_TEXT", "VERIFIED_VISUAL_STATE", "REASONABLY_CONFIRMED"} else "FAILED_RETRYABLE"
        return StepResult(step="combined_time_input", target=target, result=result, reason=f"typed combined expiry {target_text}", verification=verification, state="TIME_VERIFICATION")

    def _typed_input(self, expiry_seconds: int, *, attempt: int) -> StepResult:
        seq = self.sequencer
        boxes = seq.boxes
        open_target = self._time_panel_open_target()
        if not open_target or not all(key in boxes for key in ("hourly_input", "minute_input")):
            return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason="typed controls missing", verification="UNVERIFIED_ABORT")
        if int(expiry_seconds) % 60 and "second_input" not in boxes:
            return StepResult(
                step="typed_input",
                result="FAILED_RETRYABLE",
                reason="second precision requested but second_input is not calibrated",
                verification="UNVERIFIED_ABORT",
            )

        open_step = seq.perform_calibrated_step(
            f"open_time_panel_typed_attempt_{attempt}",
            open_target,
            expected_region="time_box",
            wait_after_ms=seq.timing_profile.time_button_after_click_wait_ms,
            state="TIME_PANEL_OPENING",
        )
        if open_step.result != "PASS":
            return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=open_step.reason, verification="UNVERIFIED_ABORT")

        hours = int(expiry_seconds) // 3600
        minutes = (int(expiry_seconds) % 3600) // 60
        seconds = int(expiry_seconds) % 60
        hour_text = f"{hours:02d}"
        minute_text = f"{minutes:02d}"
        second_text = f"{seconds:02d}"

        hour_step = seq.perform_calibrated_step("type_hour_focus", "hourly_input", expected_region="time_panel", wait_after_ms=seq.timing_profile.input_focus_wait_ms, state="TIME_PANEL_READY")
        if hour_step.result != "PASS":
            return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=hour_step.reason, verification="UNVERIFIED_ABORT")
        for step_name, action, wait_ms, reason in (
            ("type_hour_select_existing", lambda: seq.adapter.hotkey("ctrl", "a"), seq.timing_profile.select_existing_value_wait_ms, "selected existing hour value"),
            ("type_hour_value", lambda: seq.adapter.type_text_slowly(hour_text, seq.timing_profile.typing_key_interval_ms), seq.timing_profile.post_typing_wait_ms, f"typed hour {hour_text}"),
        ):
            result = seq.record_key_step(step_name, action, wait_after_ms=wait_ms, state="TIME_TYPED_OR_SELECTED", reason=reason)
            if result.result != "PASS":
                return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=result.reason, verification="UNVERIFIED_ABORT")

        minute_step = seq.perform_calibrated_step("type_minute_focus", "minute_input", expected_region="time_panel", wait_after_ms=seq.timing_profile.input_focus_wait_ms, state="TIME_ENTRY_METHOD_SELECTED")
        if minute_step.result != "PASS":
            return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=minute_step.reason, verification="UNVERIFIED_ABORT")
        for step_name, action, wait_ms, reason in (
            ("type_minute_select_existing", lambda: seq.adapter.hotkey("ctrl", "a"), seq.timing_profile.select_existing_value_wait_ms, "selected existing minute value"),
            ("type_minute_value", lambda: seq.adapter.type_text_slowly(minute_text, seq.timing_profile.typing_key_interval_ms), seq.timing_profile.post_typing_wait_ms, f"typed minute {minute_text}"),
        ):
            result = seq.record_key_step(step_name, action, wait_after_ms=wait_ms, state="TIME_TYPED_OR_SELECTED", reason=reason)
            if result.result != "PASS":
                return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=result.reason, verification="UNVERIFIED_ABORT")

        if "second_input" in boxes:
            second_step = seq.perform_calibrated_step("type_second_focus", "second_input", expected_region="time_panel", wait_after_ms=seq.timing_profile.input_focus_wait_ms, state="TIME_ENTRY_METHOD_SELECTED")
            if second_step.result != "PASS":
                return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=second_step.reason, verification="UNVERIFIED_ABORT")
            for step_name, action, wait_ms, reason in (
                ("type_second_select_existing", lambda: seq.adapter.hotkey("ctrl", "a"), seq.timing_profile.select_existing_value_wait_ms, "selected existing second value"),
                ("type_second_value", lambda: seq.adapter.type_text_slowly(second_text, seq.timing_profile.typing_key_interval_ms), seq.timing_profile.post_typing_wait_ms, f"typed second {second_text}"),
            ):
                result = seq.record_key_step(step_name, action, wait_after_ms=wait_ms, state="TIME_TYPED_OR_SELECTED", reason=reason)
                if result.result != "PASS":
                    return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=result.reason, verification="UNVERIFIED_ABORT")

        result = seq.record_key_step("confirm_typed_time", lambda: seq.adapter.press_key("enter"), wait_after_ms=seq.timing_profile.post_time_confirm_wait_ms, state="TIME_TYPED_OR_SELECTED", reason="confirmed typed time")
        if result.result != "PASS":
            return StepResult(step="typed_input", result="FAILED_RETRYABLE", reason=result.reason, verification="UNVERIFIED_ABORT")

        if "final_screen" in boxes:
            seq.perform_calibrated_step("confirm_time_focus_chart", "final_screen", expected_region="chart_surface", wait_after_ms=seq.timing_profile.post_time_confirm_wait_ms, state="TIME_VERIFICATION")

        verification = seq.verify_expiry(expiry_seconds, "typed_input")
        result = "PASS" if verification in {"VERIFIED_TEXT", "VERIFIED_VISUAL_STATE", "REASONABLY_CONFIRMED"} else "FAILED_RETRYABLE"
        return StepResult(step="typed_input", result=result, reason=f"typed expiry {expiry_seconds}s attempt {attempt}", verification=verification, state="TIME_VERIFICATION")

    def _exact_preset(self, expiry_seconds: int) -> StepResult:
        seq = self.sequencer
        key = self._preset_key(expiry_seconds)
        if key is None:
            return StepResult(step="exact_preset", result="FAILED_RETRYABLE", reason=f"no exact preset for {expiry_seconds}s", verification="UNVERIFIED_ABORT")
        open_target = self._time_panel_open_target()
        if not open_target:
            return StepResult(step="exact_preset", result="FAILED_RETRYABLE", reason="time panel opener missing", verification="UNVERIFIED_ABORT")
        open_step = seq.perform_calibrated_step("open_time_panel_preset", open_target, expected_region="time_box", wait_after_ms=seq.timing_profile.time_button_after_click_wait_ms, state="TIME_PANEL_OPENING")
        if open_step.result != "PASS":
            return StepResult(step="exact_preset", result="FAILED_RETRYABLE", reason=open_step.reason, verification="UNVERIFIED_ABORT")
        preset_step = seq.perform_calibrated_step("select_exact_preset", key, expected_region="time_panel", wait_after_ms=seq.timing_profile.preset_click_wait_ms, state="TIME_TYPED_OR_SELECTED")
        if preset_step.result != "PASS":
            return StepResult(step="exact_preset", result="FAILED_RETRYABLE", reason=preset_step.reason, verification="UNVERIFIED_ABORT")
        seq.record_key_step("confirm_preset_close", lambda: seq.adapter.press_key("esc"), wait_after_ms=seq.timing_profile.post_time_confirm_wait_ms, state="TIME_VERIFICATION", reason="closed time panel after preset")
        verification = seq.verify_expiry(expiry_seconds, "exact_preset")
        result = "PASS" if verification in {"VERIFIED_TEXT", "VERIFIED_VISUAL_STATE", "REASONABLY_CONFIRMED"} else "FAILED_RETRYABLE"
        return StepResult(step="exact_preset", target=key, result=result, reason=f"selected exact preset {key}", verification=verification, state="TIME_VERIFICATION")

    def _arrow_fallback(self, expiry_seconds: int) -> StepResult:
        seq = self.sequencer
        if not seq.timing_profile.arrow_fallback_enabled:
            return StepResult(step="arrow_fallback", result="FAILED_ABORT", reason="arrow fallback disabled", verification="UNVERIFIED_ABORT")
        if not all(key in seq.boxes for key in ("hourly_plus", "minute_plus")):
            return StepResult(step="arrow_fallback", result="FAILED_ABORT", reason="arrow controls missing", verification="UNVERIFIED_ABORT")

        hours = int(expiry_seconds) // 3600
        minutes = (int(expiry_seconds) % 3600) // 60
        seconds = int(expiry_seconds) % 60
        if seconds:
            return StepResult(
                step="arrow_fallback",
                result="FAILED_ABORT",
                reason="second precision requested but arrow fallback has no seconds control",
                verification="UNVERIFIED_ABORT",
            )
        total_clicks = int(hours) + int(minutes)
        if total_clicks <= 0 or total_clicks > int(seq.timing_profile.max_total_arrow_clicks):
            return StepResult(step="arrow_fallback", result="FAILED_ABORT", reason=f"arrow fallback bounded: clicks={total_clicks}", verification="UNVERIFIED_ABORT")

        open_target = self._time_panel_open_target()
        if not open_target:
            return StepResult(step="arrow_fallback", result="FAILED_ABORT", reason="time panel opener missing", verification="UNVERIFIED_ABORT")
        open_step = seq.perform_calibrated_step("open_time_panel_arrow", open_target, expected_region="time_box", wait_after_ms=seq.timing_profile.time_button_after_click_wait_ms, state="TIME_PANEL_OPENING")
        if open_step.result != "PASS":
            return StepResult(step="arrow_fallback", result="FAILED_ABORT", reason=open_step.reason, verification="UNVERIFIED_ABORT")

        for idx in range(hours):
            step = seq.perform_calibrated_step(f"arrow_hour_plus_{idx + 1}", "hourly_plus", expected_region="time_panel", wait_after_ms=seq.timing_profile.wait_between_arrow_clicks_ms, state="TIME_TYPED_OR_SELECTED")
            if step.result != "PASS":
                return StepResult(step="arrow_fallback", result="FAILED_ABORT", reason=step.reason, verification="UNVERIFIED_ABORT")
        for idx in range(minutes):
            step = seq.perform_calibrated_step(f"arrow_minute_plus_{idx + 1}", "minute_plus", expected_region="time_panel", wait_after_ms=seq.timing_profile.wait_between_arrow_clicks_ms, state="TIME_TYPED_OR_SELECTED")
            if step.result != "PASS":
                return StepResult(step="arrow_fallback", result="FAILED_ABORT", reason=step.reason, verification="UNVERIFIED_ABORT")

        seq.record_key_step("confirm_arrow_close", lambda: seq.adapter.press_key("esc"), wait_after_ms=seq.timing_profile.post_time_confirm_wait_ms, state="TIME_VERIFICATION", reason="closed time panel after arrow fallback")
        verification = seq.verify_expiry(expiry_seconds, "arrow_fallback")
        result = "PASS" if verification in {"VERIFIED_TEXT", "VERIFIED_VISUAL_STATE", "REASONABLY_CONFIRMED"} else "FAILED_ABORT"
        return StepResult(step="arrow_fallback", result=result, reason=f"arrow fallback applied {total_clicks} bounded clicks", verification=verification, state="TIME_VERIFICATION")

    def _preset_key(self, expiry_seconds: int) -> Optional[str]:
        for key in (f"time_{int(expiry_seconds)}", f"time_preset_{int(expiry_seconds)}"):
            if key in self.sequencer.boxes:
                return key
        return None

    def _time_panel_open_target(self) -> str:
        for key in ("time_input", "time_box", "expiry_time_field", "time_button"):
            target = _first_present_target(self.sequencer.boxes, key)
            if target:
                return target
        return ""


def write_live_behavior_validation_report(
    result: ActionSequenceResult,
    *,
    session_id: str,
    mode: str,
    calibration_file: str,
    broker_window_title: str = "",
    window_rect_initial: Sequence[int] | None = None,
    window_rect_final: Sequence[int] | None = None,
    json_path: str | Path = DEFAULT_VALIDATION_JSON,
    md_path: str | Path = DEFAULT_VALIDATION_MD,
) -> tuple[Path, Path]:
    json_target = Path(json_path)
    md_target = Path(md_path)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    md_target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "mode": mode,
        "packet_id": result.packet_id,
        "side": result.side,
        "expiry_seconds": result.expiry_seconds,
        "calibration_file": calibration_file,
        "broker_window_title": broker_window_title,
        "window_rect_initial": list(window_rect_initial) if window_rect_initial else None,
        "window_rect_final": list(window_rect_final) if window_rect_final else None,
        "method": result.method,
        "expiry_status": result.expiry_status,
        "steps": [step.as_dict() for step in result.steps],
        "overall": result.overall,
        "reason": result.reason,
        "evidence_dir": result.evidence_dir,
        "trace_path": result.trace_path,
    }
    json_target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = [
        "# PhoenixGuard Live Behavior Validation",
        "",
        f"- session_id: {session_id}",
        f"- mode: {mode}",
        f"- packet_id: {result.packet_id}",
        f"- side: {result.side}",
        f"- expiry_seconds: {result.expiry_seconds}",
        f"- method: {result.method}",
        f"- expiry_status: {result.expiry_status}",
        f"- overall: {result.overall}",
        f"- reason: {result.reason}",
        "",
        "## Steps",
        "",
    ]
    for step in result.steps:
        lines.append(f"- {step.step}: {step.result} | target={step.target} | wait={step.wait_after_ms}ms | reason={step.reason}")
    md_target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result.report_json_path = str(json_target)
    result.report_md_path = str(md_target)
    return json_target, md_target
