from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


class ShooterMode(str, Enum):
    STUDY_ONLY = "STUDY_ONLY"
    PAPER_EXECUTION = "PAPER_EXECUTION"
    DRY_RUN_CLICK = "DRY_RUN_CLICK"
    CALIBRATION_TEST = "CALIBRATION_TEST"
    LIVE_DISABLED = "LIVE_DISABLED"
    LIVE_READY = "LIVE_READY"
    LIVE_BEHAVIOR_VALIDATION = "LIVE_BEHAVIOR_VALIDATION"


DEFAULT_SHOOTER_MODE = ShooterMode.LIVE_DISABLED
SHOOTER_MODE_CHOICES = tuple(mode.value for mode in ShooterMode)

SHOOTER_VALIDATION_DIR = Path("data") / "shooter_validation"
PAPER_EXECUTION_LOG = SHOOTER_VALIDATION_DIR / "paper_executions.jsonl"
DRY_RUN_CLICK_LOG = SHOOTER_VALIDATION_DIR / "dry_run_clicks.jsonl"
CALIBRATION_TEST_LOG = SHOOTER_VALIDATION_DIR / "calibration_tests.jsonl"
LIVE_DISABLED_LOG = SHOOTER_VALIDATION_DIR / "live_disabled.jsonl"
LIVE_READY_LOG = SHOOTER_VALIDATION_DIR / "live_ready.jsonl"
LIVE_BEHAVIOR_VALIDATION_LOG = SHOOTER_VALIDATION_DIR / "live_behavior_validation.jsonl"


@dataclass(frozen=True)
class ShooterModeResult:
    mode: ShooterMode
    recorded: bool
    broker_click_allowed: bool
    reason: str
    record_path: str | None = None


def resolve_shooter_mode(raw: Any = None) -> ShooterMode:
    if isinstance(raw, ShooterMode):
        return raw
    text = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not text:
        return DEFAULT_SHOOTER_MODE
    try:
        return ShooterMode(text)
    except ValueError as exc:
        valid = ", ".join(SHOOTER_MODE_CHOICES)
        raise ValueError(f"Unsupported shooter mode '{raw}'. Expected one of: {valid}") from exc


def broker_clicks_allowed(mode: ShooterMode | str) -> bool:
    return resolve_shooter_mode(mode) in {ShooterMode.LIVE_READY, ShooterMode.LIVE_BEHAVIOR_VALIDATION}


def _clean_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _packet_id(packet: Mapping[str, Any]) -> str:
    return str(packet.get("packet_id") or packet.get("decision_id") or "").strip()


def _execution(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    value = packet.get("execution")
    return value if isinstance(value, Mapping) else {}


def _base_record(packet: Mapping[str, Any], decision: Mapping[str, Any], mode: ShooterMode, now: float | None) -> dict[str, Any]:
    execution = _execution(packet)
    timestamp = time.time() if now is None else float(now)
    return {
        "timestamp": timestamp,
        "mode": mode.value,
        "packet_id": _packet_id(packet),
        "session_id": str(packet.get("session_id") or ""),
        "symbol": str(packet.get("symbol") or ""),
        "timeframe": str(packet.get("timeframe") or ""),
        "side": str(decision.get("side") or execution.get("side") or "").upper(),
        "expiry_seconds": decision.get("expiry_seconds") or execution.get("expiry_seconds"),
        "decision_reason": str(decision.get("reason") or ""),
        "schema_version": str(packet.get("schema_version") or ""),
        "broker_click_allowed": False,
    }


def append_jsonl(path: Path, record: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), sort_keys=True, default=str) + "\n")
    return path


def record_paper_execution(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    path: Path | None = None,
    now: float | None = None,
) -> ShooterModeResult:
    mode = ShooterMode.PAPER_EXECUTION
    record = _base_record(packet, decision, mode, now)
    record["paper_filled"] = True
    record_path = append_jsonl(path or PAPER_EXECUTION_LOG, record)
    return ShooterModeResult(mode, True, False, "PAPER_EXECUTION_RECORDED", str(record_path))


def _point_from_box(bounds: Sequence[int], box: Mapping[str, Any]) -> tuple[int, int] | None:
    rel_x = _clean_float(box.get("x"))
    rel_y = _clean_float(box.get("y"))
    if rel_x is None or rel_y is None:
        return None
    left, top, right, bottom = [int(value) for value in bounds[:4]]
    width = max(1, right - left)
    height = max(1, bottom - top)
    return left + int(width * rel_x), top + int(height * rel_y)


def build_coordinate_report(
    boxes: Mapping[str, Any],
    bounds: Sequence[int],
    *,
    side: str,
    expiry_seconds: int,
) -> dict[str, Any]:
    side_key = "buy_icon" if str(side).upper() == "BUY" else "sell_icon"
    candidate_keys = [
        "time_button" if "time_button" in boxes else "time_box",
        side_key,
    ]
    for key in (f"time_{int(expiry_seconds)}", f"time_preset_{int(expiry_seconds)}"):
        if key in boxes:
            candidate_keys.append(key)
            break
    for key in ("hourly_input", "minute_input"):
        if key in boxes:
            candidate_keys.append(key)

    left, top, right, bottom = [int(value) for value in bounds[:4]]
    points: dict[str, dict[str, int]] = {}
    errors: list[str] = []
    for key in candidate_keys:
        raw_box = boxes.get(key)
        if not isinstance(raw_box, Mapping):
            errors.append(f"MISSING_BOX:{key}")
            continue
        point = _point_from_box(bounds, raw_box)
        if point is None:
            errors.append(f"INVALID_BOX:{key}")
            continue
        x, y = point
        if x < left or x > right or y < top or y > bottom:
            errors.append(f"OUT_OF_BOUNDS:{key}")
            continue
        points[key] = {"x": int(x), "y": int(y)}

    return {
        "ok": not errors,
        "bounds": {"left": left, "top": top, "right": right, "bottom": bottom},
        "points": points,
        "errors": errors,
    }


def record_dry_run_click(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    coordinate_report: Mapping[str, Any],
    *,
    path: Path | None = None,
    now: float | None = None,
) -> ShooterModeResult:
    mode = ShooterMode.DRY_RUN_CLICK
    record = _base_record(packet, decision, mode, now)
    record["coordinate_report"] = dict(coordinate_report)
    record_path = append_jsonl(path or DRY_RUN_CLICK_LOG, record)
    return ShooterModeResult(mode, True, False, "DRY_RUN_CLICK_RECORDED", str(record_path))


def record_calibration_test(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    coordinate_report: Mapping[str, Any],
    *,
    path: Path | None = None,
    now: float | None = None,
) -> ShooterModeResult:
    mode = ShooterMode.CALIBRATION_TEST
    record = _base_record(packet, decision, mode, now)
    record["coordinate_report"] = dict(coordinate_report)
    record_path = append_jsonl(path or CALIBRATION_TEST_LOG, record)
    return ShooterModeResult(mode, True, False, "CALIBRATION_TEST_RECORDED", str(record_path))


def record_live_disabled(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    path: Path | None = None,
    now: float | None = None,
) -> ShooterModeResult:
    mode = ShooterMode.LIVE_DISABLED
    record = _base_record(packet, decision, mode, now)
    record["blocked_reason"] = "LIVE_BROKER_CLICKS_DISABLED"
    record_path = append_jsonl(path or LIVE_DISABLED_LOG, record)
    return ShooterModeResult(mode, True, False, "LIVE_DISABLED_RECORDED", str(record_path))


def record_live_ready(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    clicked: bool,
    reason: str,
    rehearsal: Mapping[str, Any] | None = None,
    path: Path | None = None,
    now: float | None = None,
) -> ShooterModeResult:
    mode = ShooterMode.LIVE_READY
    record = _base_record(packet, decision, mode, now)
    record["broker_click_allowed"] = True
    record["clicked"] = bool(clicked)
    record["live_ready_reason"] = str(reason or "")
    if rehearsal is not None:
        record["execution_rehearsal"] = dict(rehearsal)
    record_path = append_jsonl(path or LIVE_READY_LOG, record)
    return ShooterModeResult(mode, True, True, str(reason or "LIVE_READY_RECORDED"), str(record_path))


def record_live_behavior_validation(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    clicked: bool,
    reason: str,
    action_report: Mapping[str, Any] | None = None,
    path: Path | None = None,
    now: float | None = None,
) -> ShooterModeResult:
    mode = ShooterMode.LIVE_BEHAVIOR_VALIDATION
    record = _base_record(packet, decision, mode, now)
    record["broker_click_allowed"] = True
    record["clicked"] = bool(clicked)
    record["live_behavior_validation_reason"] = str(reason or "")
    if action_report is not None:
        record["action_sequence"] = dict(action_report)
    record_path = append_jsonl(path or LIVE_BEHAVIOR_VALIDATION_LOG, record)
    return ShooterModeResult(mode, True, True, str(reason or "LIVE_BEHAVIOR_VALIDATION_RECORDED"), str(record_path))
