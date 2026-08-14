#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

from _pg_bootstrap import ensure_project_paths

PROJECT_ROOT = ensure_project_paths()

def _default_live_runtime_dir(project_root: Path | None = None) -> Path:
    resolved_project_root = project_root or PROJECT_ROOT
    configured_runtime_dir = str(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or "").strip()
    if configured_runtime_dir:
        return Path(configured_runtime_dir).expanduser()
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser() / "PhoenixGuard" / "runtime" / "live"
    return resolved_project_root / "runtime" / "live"


DEFAULT_OUTPUT = _default_live_runtime_dir() / "trigger_calibration_manifest.json"
DEFAULT_CHART_FOCUS_SETTLE_SECONDS = 0.0
DEFAULT_PRE_CLICK_DELAY_SECONDS = 5.0
DEFAULT_POINTER_MOVE_DURATION_SECONDS = 0.35


def _pause_after_calibration() -> None:
    print("\nCalibration complete. Pausing for 5 seconds before exit...\n")
    time.sleep(5)


def _read_click(label: str) -> tuple[int, int]:
    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover - environment-dependent.
        raise RuntimeError("pyautogui is required for calibration capture. Install it in the active environment.") from exc

    print(f"\n1) Hover over the {label}.")
    print("2) Press Enter to record the current cursor position.")
    print("3) Do not click. Only hover and press Enter.\n")
    user_input = input("Hover over the target and press Enter to record it...\n")
    _ = user_input
    x, y = pyautogui.position()
    print(f"Captured {label}: ({x}, {y})")
    return int(x), int(y)


def _build_manifest(
    *,
    chart_anchor: tuple[int, int],
    buy_click: tuple[int, int],
    sell_click: tuple[int, int],
    fixed_expiry_seconds: int,
    fixed_amount: float,
    score_threshold: float,
    chart_focus_settle_seconds: float,
    pre_click_delay_seconds: float,
    pointer_move_duration_seconds: float,
) -> dict[str, Any]:
    return {
        "version": "phoenixguard_trigger_calibration_v1",
        "broker": "Pocket Option",
        "mode": "fixed_amount_fixed_expiry",
        "fixed_amount": float(fixed_amount),
        "fixed_expiry_seconds": int(fixed_expiry_seconds),
        "score_threshold": float(score_threshold),
        "trigger_order": ["chart_anchor", "buy_click", "sell_click"],
        "boxes": {
            "chart_anchor": {"x": chart_anchor[0], "y": chart_anchor[1]},
            "buy_click": {"x": buy_click[0], "y": buy_click[1]},
            "sell_click": {"x": sell_click[0], "y": sell_click[1]},
        },
        "actions": {
            "buy": {"click": {"x": buy_click[0], "y": buy_click[1]}},
            "sell": {"click": {"x": sell_click[0], "y": sell_click[1]}},
            "chart_focus": {"click": {"x": chart_anchor[0], "y": chart_anchor[1]}},
        },
        "trigger_policy": {
            "chart_focus_before_trade": True,
            "time_fixed": True,
            "amount_fixed": True,
            "allow_buy_sell_auto_switch": True,
            "weighted_score_required": True,
        },
        "timing_policy": {
            "chart_focus_settle_seconds": float(chart_focus_settle_seconds),
            "pre_click_delay_seconds": float(pre_click_delay_seconds),
            "pointer_move_duration_seconds": float(pointer_move_duration_seconds),
        },
        "notes": "The bridge will focus the chart, use one saved pacing wait, then click the BUY or SELL target based on the live PhoenixGuard action. Time and amount remain fixed to the values below.",
    }


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(manifest, indent=2, sort_keys=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    backup = path.with_name("trigger_calibration_manifest.backup.json")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    backup.write_text(encoded, encoding="utf-8")
    print(f"\nCalibration saved to: {path}")
    print(f"Calibration backup saved to: {backup}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture the Pocket Option trigger points that the direct trade bridge will use after PhoenixGuard ingests the live market state.",
    )
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--fixed-expiry-seconds", type=int, default=60)
    parser.add_argument("--fixed-amount", type=float, default=1.0)
    parser.add_argument("--score-threshold", type=float, default=0.62)
    parser.add_argument("--chart-focus-settle-seconds", type=float, default=DEFAULT_CHART_FOCUS_SETTLE_SECONDS)
    parser.add_argument("--pre-click-delay-seconds", type=float, default=DEFAULT_PRE_CLICK_DELAY_SECONDS)
    parser.add_argument("--pointer-move-duration-seconds", type=float, default=DEFAULT_POINTER_MOVE_DURATION_SECONDS)
    args = parser.parse_args()

    print("PhoenixGuard trigger calibration")
    print("This records the chart focus point and the BUY/SELL click points for your Pocket Option trigger.")
    print("The bridge will use the live PhoenixGuard trade signal and send the order to the calibrated trigger.")
    print("Time and amount are fixed, so no time input or amount adjustment is required during live trading.")
    print("\nCalibration flow:")
    print("1) Hover the chart area and left-click once.")
    print("2) Press Enter to save it.")
    print("3) Move to the SELL button, left-click once, and press Enter to save it.")
    print("4) Return to the chart area, left-click once, and press Enter to save it.")
    print("5) Move to the BUY button, left-click once, and press Enter to save it.")
    print("6) Return to the chart area, left-click once, and press Enter to save it.")
    print("7) After the calibration is done, the script pauses for 5 seconds before finishing.\n")

    chart_anchor = _read_click("chart anchor")
    sell_click = _read_click("SELL trigger")
    chart_anchor = _read_click("chart anchor")
    buy_click = _read_click("BUY trigger")
    chart_anchor = _read_click("chart anchor")
    _pause_after_calibration()

    manifest = _build_manifest(
        chart_anchor=chart_anchor,
        buy_click=buy_click,
        sell_click=sell_click,
        fixed_expiry_seconds=args.fixed_expiry_seconds,
        fixed_amount=args.fixed_amount,
        score_threshold=args.score_threshold,
        chart_focus_settle_seconds=max(0.0, float(args.chart_focus_settle_seconds)),
        pre_click_delay_seconds=max(0.0, float(args.pre_click_delay_seconds)),
        pointer_move_duration_seconds=max(0.0, float(args.pointer_move_duration_seconds)),
    )
    output_path = Path(args.output).expanduser()
    _write_manifest(output_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
