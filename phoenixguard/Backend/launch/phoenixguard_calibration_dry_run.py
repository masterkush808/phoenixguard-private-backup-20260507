#!/usr/bin/env python3
"""Visual dry-run of the saved trigger calibration.

Walks the exact pointer path a real trade would take - chart anchor focus,
move to the calibrated button, the double-press position - for BOTH sides,
without ever clicking. Uses the same manifest loader, box resolution, and
timing policy as phoenixguard_direct_trade_bridge.py so what you see is
precisely what a live BUY or SELL would do.

Usage:
    python Backend/launch/phoenixguard_calibration_dry_run.py            # move for both sides
    python Backend/launch/phoenixguard_calibration_dry_run.py --side buy # one side only
    python Backend/launch/phoenixguard_calibration_dry_run.py --list     # print boxes only
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

from _pg_bootstrap import ensure_project_paths  # noqa: E402

PROJECT_ROOT = ensure_project_paths()

from phoenixguard_direct_trade_bridge import (  # noqa: E402
    DEFAULT_POINTER_MOVE_DURATION_SECONDS,
    MissingCalibration,
    _load_calibration_manifest,
    _send_direct_clicks,
    _trigger_manifest_to_boxes,
)


def _resolve_button(boxes: dict[str, tuple[int, int]], side: str) -> tuple[str, tuple[int, int]]:
    key = "buy_click" if side == "BUY" else "sell_click"
    if key in boxes:
        return key, boxes[key]
    fallback = "buy_button" if side == "BUY" else "sell_button"
    if fallback in boxes:
        return fallback, boxes[fallback]
    raise MissingCalibration(f"No calibrated {side} click target found in the manifest.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phoenixguard_calibration_dry_run.py",
        description="Dry-run the saved trigger calibration: visual pointer path for BUY and SELL, no clicks.",
    )
    parser.add_argument("--calibration-path", default="", help="Override path to trigger_calibration_manifest.json.")
    parser.add_argument("--side", choices=("buy", "sell", "both"), default="both")
    parser.add_argument("--repeats", type=int, default=1, help="How many times to walk each side.")
    parser.add_argument("--countdown", type=float, default=3.0, help="Seconds before the pointer starts moving.")
    parser.add_argument("--hold-seconds", type=float, default=1.5, help="Pause on the button position (simulating the press).")
    parser.add_argument("--list", action="store_true", help="Print resolved calibration and exit without moving.")
    parser.add_argument(
        "--click",
        action="store_true",
        help="LIVE TEST: perform the real clicks (focus + double press) via the bridge's own click path.",
    )
    args = parser.parse_args(argv)

    try:
        import pyautogui
    except Exception:  # pragma: no cover
        print("pyautogui is required (it is installed in .venv-live).")
        return 1

    try:
        manifest = _load_calibration_manifest(args.calibration_path or None)
    except MissingCalibration as exc:
        print(f"CALIBRATION MISSING: {exc}")
        return 1

    boxes, chart_anchor, fixed_amount, fixed_expiry, timing_policy = (
        _trigger_manifest_to_boxes(manifest)
    )

    print("=== Saved calibration ===")
    for name, (x, y) in sorted(boxes.items()):
        print(f"  {name:12s} -> ({x}, {y})")
    print(f"  chart_anchor -> {chart_anchor}")
    print(f"  fixed_amount={fixed_amount} fixed_expiry={fixed_expiry}s")
    print(f"  timing_policy={timing_policy}")

    if args.list:
        return 0

    sides = ["BUY", "SELL"] if args.side == "both" else [args.side.upper()]
    move_duration = max(
        0.05,
        float(timing_policy.get("pointer_move_duration_seconds", DEFAULT_POINTER_MOVE_DURATION_SECONDS)),
    )

    if args.click:
        print(f"\nLIVE CLICK TEST in {args.countdown:.0f}s - real orders WILL be placed at the calibrated buttons.")
        time.sleep(max(0.0, args.countdown))
        for side in sides:
            key, (x, y) = _resolve_button(boxes, side)
            print(f"\n--- {side} via '{key}' at ({x}, {y}) ---")
            meta = _send_direct_clicks(
                boxes,
                chart_anchor=chart_anchor,
                side=side,
                expiry_seconds=fixed_expiry,
                fixed_amount=fixed_amount,
                timing_policy=timing_policy,
            )
            print(f"  clicked: {meta}")
        print("\nLive click test complete.")
        return 0

    print(f"\nStarting in {args.countdown:.0f}s - slam the mouse into a screen corner to abort (pyautogui failsafe).")
    time.sleep(max(0.0, args.countdown))

    original_position = pyautogui.position()
    for repeat in range(1, max(1, args.repeats) + 1):
        for side in sides:
            key, (x, y) = _resolve_button(boxes, side)
            print(f"\n--- [{repeat}] {side} via '{key}' at ({x}, {y}) ---")
            if chart_anchor is not None:
                cx, cy = chart_anchor
                print(f"  1. focus-click target: move to chart_anchor ({cx}, {cy}) [NO CLICK]")
                pyautogui.moveTo(cx, cy, duration=move_duration)
                time.sleep(0.4)
            print(f"  2. move to {side} button ({x}, {y})")
            pyautogui.moveTo(x, y, duration=move_duration)
            print(f"  3. hold {args.hold_seconds:.1f}s on the double-press position [NO CLICK]")
            time.sleep(max(0.1, args.hold_seconds))
            print(f"  OK: {side} path verified (2 presses would land here).")

    pyautogui.moveTo(original_position.x, original_position.y, duration=move_duration)
    print("\nDone. No clicks were sent. Calibration path verified for:", ", ".join(sides))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
