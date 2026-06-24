from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast


TIME_STEPS = {
    "focus_combined_time_input",
    "type_combined_time_select_existing",
    "type_combined_time_value",
    "confirm_combined_time",
    "confirm_combined_time_focus_chart",
    "open_time_panel_typed_attempt_1",
    "open_time_panel_typed_attempt_2",
    "open_time_panel_preset",
    "open_time_panel_arrow",
    "type_hour_focus",
    "type_minute_focus",
    "select_exact_preset",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"_parse_error": line})
            continue
        if isinstance(value, Mapping):
            rows.append(dict(cast(Mapping[str, Any], value)))
    return rows


def _box_targets(boxes: Mapping[str, Any]) -> set[str]:
    return {str(key) for key, value in boxes.items() if isinstance(value, Mapping) and key != "capabilities"}


def _int_value(value: object, default: int = 0) -> int:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float_value(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def analyze_trace(rows: list[dict[str, Any]], boxes: Mapping[str, Any]) -> list[str]:
    findings: list[str] = []
    targets = _box_targets(boxes)
    if not rows:
        return ["TRACE_MISSING_OR_EMPTY"]

    final_indices = [idx for idx, row in enumerate(rows) if row.get("step") == "final_side_click"]
    time_indices = [idx for idx, row in enumerate(rows) if row.get("step") in TIME_STEPS]
    final_hold_rows = [row for row in rows if row.get("step") == "final_pre_side_click_hold"]

    if final_indices and not time_indices:
        findings.append("BUY_SELL_CLICKED_BEFORE_EXPIRY_SEQUENCE_COMPLETED")
    if final_indices and time_indices and min(final_indices) < max(time_indices):
        findings.append("BUY_SELL_CLICK_APPEARS_BEFORE_TIME_INPUT_SEQUENCE_FINISHED")
    if not time_indices:
        findings.append("TIME_PANEL_OR_TIME_INPUT_STEP_MISSING")
    if final_indices and not final_hold_rows:
        findings.append("FINAL_PRE_SIDE_CLICK_HOLD_MISSING")
    for row in final_hold_rows:
        try:
            hold_ms = int(row.get("wait_after_ms") or 0)
        except (TypeError, ValueError):
            hold_ms = 0
        if hold_ms < 250:
            findings.append(f"FINAL_PRE_SIDE_CLICK_HOLD_UNDERRUN:{hold_ms}ms")

    final_count = len(final_indices)
    if final_count > 1:
        findings.append(f"DOUBLE_SIDE_CLICK_DETECTED:{final_count}")

    previous_window = None
    previous_ts = None
    for idx, row in enumerate(rows):
        if "_parse_error" in row:
            findings.append(f"TRACE_PARSE_ERROR_LINE:{idx + 1}")
            continue
        target = str(row.get("target") or "")
        if target and target not in {"keyboard", "timer", "abort"} and target not in targets:
            findings.append(f"UNKNOWN_TARGET:{target}:line{idx + 1}")
        if str(row.get("result") or "").upper().startswith("FAILED"):
            findings.append(f"FAILED_STEP:{row.get('step')}:{row.get('reason')}")
        rect = row.get("window_rect")
        if isinstance(rect, list):
            rect_values = cast(list[Any], rect)
            if len(rect_values) < 4:
                continue
            rect_tuple = tuple(int(v) for v in cast(Sequence[Any], rect_values)[:4])
            if previous_window and rect_tuple != previous_window:
                old_w = previous_window[2] - previous_window[0]
                old_h = previous_window[3] - previous_window[1]
                new_w = rect_tuple[2] - rect_tuple[0]
                new_h = rect_tuple[3] - rect_tuple[1]
                if abs(old_w - new_w) > 40 or abs(old_h - new_h) > 40:
                    findings.append(f"WINDOW_MOVED_OR_RESIZED_BETWEEN_STEPS:{idx}")
            previous_window = rect_tuple
        wait_ms = _int_value(row.get("wait_after_ms"))
        if target not in {"keyboard", "timer"} and wait_ms and wait_ms < 180:
            findings.append(f"TIMING_UNDERRUN:{row.get('step')}:{wait_ms}ms")
        ts_float = _float_value(row.get("timestamp"))
        if previous_ts and ts_float and ts_float < previous_ts:
            findings.append(f"NON_MONOTONIC_TRACE_TIMESTAMP:line{idx + 1}")
        if ts_float:
            previous_ts = ts_float

    fallback_steps = [str(row.get("step") or "") for row in rows if "preset" in str(row.get("step") or "") or "arrow" in str(row.get("step") or "")]
    if fallback_steps:
        findings.append("FALLBACK_PATH_USED:" + ",".join(fallback_steps[:8]))

    return findings or ["PASS_NO_CALIBRATION_BEHAVIOR_GAPS_DETECTED"]


def write_report(findings: list[str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Shooter Calibration Behavior Gap Report", ""]
    for finding in findings:
        lines.append(f"- {finding}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze PhoenixGuard shooter calibrated action trace.")
    parser.add_argument("--trace", required=True, help="Path to action_trace.jsonl")
    parser.add_argument("--boxes", required=True, help="Path to 808_shooter_boxes.json")
    parser.add_argument("--output", default="calibration_behavior_gap_report.md", help="Markdown output path")
    args = parser.parse_args()

    trace_path = Path(args.trace)
    boxes_path = Path(args.boxes)
    rows = _load_jsonl(trace_path)
    boxes = _load_json(boxes_path)
    if not isinstance(boxes, Mapping):
        raise SystemExit("boxes file must contain a JSON object")
    findings = analyze_trace(rows, dict(cast(Mapping[str, Any], boxes)))
    output = Path(args.output)
    write_report(findings, output)
    for finding in findings:
        print(finding)
    print(f"report={output}")
    return 0 if findings == ["PASS_NO_CALIBRATION_BEHAVIOR_GAPS_DETECTED"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
