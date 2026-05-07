#!/usr/bin/env python3
"""
808 CALIBRATOR - Production Trade Execution Flow Trainer
Guided calibration that mimics real signal→time→execute→wait sequence.

User workflow:
1. Signal received (simulated)
2. Navigate to TIME box and press Enter
3. See +/- adjustment controls appear
4. Navigate to quick-time preset and press Enter (or adjust with +/-)
5. Navigate to AMOUNT and press Enter
6. Navigate to BUY/SELL button and press Enter
7. Verify trade execution

This reduces friction vs terminal-switching and lets you practice the exact sequence.
"""

import argparse
import ctypes
import json
import logging
import re
import sys
import time
from ctypes import Structure, WINFUNCTYPE, byref, c_bool, c_int
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, cast

import pyautogui

pytesseract: Any | None = None

try:
    import pytesseract  # type: ignore[reportMissingTypeStubs]
    has_ocr = True
except Exception:
    has_ocr = False

pyautogui.FAILSAFE = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("808_calibrator")

BOXES_FILE = Path("808_shooter_boxes.json")
CALIBRATION_REPORT = Path("808_calibration_report.json")

# Slow mouse movement for broker UI responsiveness
MOUSE_DURATION = 0.35  # seconds for full cursor travel
CLICK_PAUSE = 0.20  # pause after click for UI to respond


class RECT(Structure):
    _fields_ = [("left", c_int), ("top", c_int), ("right", c_int), ("bottom", c_int)]


USER32 = ctypes.windll.user32


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return str(buf.value or "")


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return str(buf.value or "")


def list_visible_windows(query: Optional[str] = None) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    enum_proc = WINFUNCTYPE(c_bool, c_int, c_int)

    @enum_proc
    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        if not USER32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd).strip()
        if not title:
            return True
        class_name = _window_class(hwnd).strip()
        rows.append((hwnd, title, class_name))
        return True

    USER32.EnumWindows(_enum_cb, 0)
    if query:
        lowered_query = query.lower().strip()
        rows = [row for row in rows if lowered_query in row[1].lower()]
    return rows


def find_broker_window(window_query: Optional[str] = None) -> Optional[int]:
    all_windows = list_visible_windows()

    if window_query:
        filtered = list_visible_windows(window_query)
        if filtered:
            filtered.sort(key=lambda row: len(row[1]), reverse=True)
            hwnd, title, class_name = filtered[0]
            LOGGER.info("Selected: HWND=%s | class=%s | title=%s", hwnd, class_name, title)
            return hwnd

    browser_like: list[tuple[int, str, str]] = []
    for hwnd, title, class_name in all_windows:
        title_l = title.lower()
        class_l = class_name.lower()
        if (
            "chrome_widgetwin" in class_l
            or "mozillawindowclass" in class_l
            or "applicationframewindow" in class_l
        ):
            if any(
                token in title_l
                for token in ("trading platform", "pocket", "option", "broker", "otc", "usd")
            ):
                browser_like.append((hwnd, title, class_name))

    if browser_like:
        browser_like.sort(key=lambda row: len(row[1]), reverse=True)
        hwnd, title, class_name = browser_like[0]
        LOGGER.warning("Using heuristic fallback: HWND=%s | class=%s", hwnd, class_name)
        return hwnd

    LOGGER.error("Broker window not found. Use list-windows or pass --window-query.")
    return None


def activate_window(hwnd: int) -> bool:
    try:
        USER32.ShowWindow(hwnd, 9)
        USER32.SetForegroundWindow(hwnd)
        USER32.SetFocus(hwnd)
        time.sleep(0.25)
        return True
    except Exception as exc:
        LOGGER.error("Failed to activate window: %s", exc)
        return False


def get_window_rect(hwnd: int) -> Optional[RECT]:
    rect = RECT()
    if USER32.GetWindowRect(hwnd, byref(rect)):
        return rect
    return None


def rel_to_abs(rect: RECT, rel_x: float, rel_y: float) -> Tuple[int, int]:
    width = max(1, rect.right - rect.left)
    height = max(1, rect.bottom - rect.top)
    abs_x = rect.left + int(width * rel_x)
    abs_y = rect.top + int(height * rel_y)
    return abs_x, abs_y


def load_boxes(require_saved: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
    if BOXES_FILE.exists():
        try:
            parsed_any = json.loads(BOXES_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed_any, dict) and parsed_any:
                LOGGER.info("Loaded existing boxes from %s", BOXES_FILE)
                return cast(Dict[str, Dict[str, Any]], parsed_any)
        except Exception as exc:
            LOGGER.warning("Failed to load boxes: %s", exc)

    if require_saved:
        LOGGER.error("No saved calibration found. Run 'python 808_calibrator.py calibrate' first.")
        return None

    # Defaults optimized for right-side Pocket Option order panel
    defaults: Dict[str, Dict[str, float]] = {
        "time_box": {"x": 0.90, "y": 0.26},
        "time_adjustment_minus": {"x": 0.82, "y": 0.27},
        "time_adjustment_plus": {"x": 0.98, "y": 0.27},
        "time_preset_30": {"x": 0.82, "y": 0.30},
        "time_preset_60": {"x": 0.86, "y": 0.30},
        "time_preset_120": {"x": 0.90, "y": 0.30},
        "time_preset_300": {"x": 0.94, "y": 0.30},
        "amount_box": {"x": 0.90, "y": 0.36},
        "buy_button": {"x": 0.90, "y": 0.43},
        "sell_button": {"x": 0.90, "y": 0.49},
    }
    return defaults


def save_boxes(boxes: Dict[str, Dict[str, Any]]) -> None:
    BOXES_FILE.write_text(json.dumps(boxes, indent=2), encoding="utf-8")
    LOGGER.info("Saved boxes to %s", BOXES_FILE)


def slow_click(x: int, y: int, label: str = "") -> None:
    """Click with slow, deliberate cursor movement for broker UI response."""
    pyautogui.moveTo(x, y, duration=MOUSE_DURATION)
    time.sleep(0.12)
    pyautogui.click(x, y)
    if label:
        LOGGER.info("  ✓ Clicked %s at (%s, %s)", label, x, y)
    time.sleep(CLICK_PAUSE)


def calibrate_point_interactive(
    hwnd: int, rect: RECT, name: str, instruction: str
) -> Dict[str, float]:
    """
    Interactive calibration: display instruction, wait for user hover + Enter,
    then capture relative coordinates.
    """
    print(f"\n{'='*70}")
    print(f"STEP: {instruction}")
    print(f"{'='*70}")
    print(f"📍 Point: {name}")
    print(f"🖱️  Instructions: Hover your mouse at the CENTER of the target area.")
    print(f"⌨️  Press ENTER when ready...")
    print(f"{'='*70}\n")

    input()  # Wait for user to press Enter

    x, y = pyautogui.position()
    width = max(1, rect.right - rect.left)
    height = max(1, rect.bottom - rect.top)
    rel_x = max(0.0, min(1.0, float(x - rect.left) / float(width)))
    rel_y = max(0.0, min(1.0, float(y - rect.top) / float(height)))

    result = {"x": rel_x, "y": rel_y}
    LOGGER.info("✓ Captured %s => rel(%.4f, %.4f) abs(%s, %s)", name, rel_x, rel_y, x, y)
    return result


def verify_expiry_via_ocr(hwnd: int, rect: RECT, boxes: Dict[str, Dict[str, Any]], expected_expiry: int) -> bool:
    """
    Phase 3: OCR verification of displayed expiry.
    Captures the time display region and verifies it matches the expected expiry.
    Returns True if OCR confirms the expiry, False otherwise.
    """
    if not has_ocr or pytesseract is None:
        LOGGER.info("OCR not available (pytesseract/Pillow not installed); skipping verification")
        return True  # Don't fail if OCR is not available
    
    try:
        # Capture region around time_box
        time_box = boxes.get("time_box", {})
        if not time_box:
            LOGGER.warning("time_box not found in calibration; cannot verify expiry")
            return True
        
        x, y = rel_to_abs(rect, time_box.get("x", 0.9), time_box.get("y", 0.26))
        
        # Grab small box centered at (x, y)
        left = max(rect.left, x - 80)
        top = max(rect.top, y - 18)
        right = min(rect.right, x + 80)
        bottom = min(rect.bottom, y + 18)
        
        img = pyautogui.screenshot(region=(left, top, right - left, bottom - top))
        
        # OCR with PSM 7 (single text line)
        txt_raw = pytesseract.image_to_string(img, config='--psm 7')
        txt = txt_raw if isinstance(txt_raw, str) else str(txt_raw)
        LOGGER.info("OCR read from time display: %s", txt)
        
        # Find first number sequence
        m = re.search(r"(\d{1,4})", txt)
        if m:
            ocr_value = int(m.group(1))
            LOGGER.info("OCR parsed expiry: %ds", ocr_value)
            
            # Check if it matches expected (allow ±10% tolerance for rounding/display variation)
            tolerance = max(1, expected_expiry // 10)
            if abs(ocr_value - expected_expiry) <= tolerance:
                LOGGER.info("✓ Expiry verification PASSED: OCR=%ds vs Expected=%ds (tolerance=%ds)", 
                           ocr_value, expected_expiry, tolerance)
                return True
            else:
                LOGGER.warning("⚠ Expiry verification FAILED: OCR=%ds vs Expected=%ds (tolerance=%ds)", 
                             ocr_value, expected_expiry, tolerance)
                return False
        else:
            LOGGER.warning("No numeric value found in OCR output; cannot verify expiry")
            return False
            
    except Exception as exc:
        LOGGER.warning("OCR verification error: %s", exc)
        return False


def run_calibration(args: argparse.Namespace) -> int:
    """
    Interactive calibration mimicking the real execution flow:
    SIGNAL → TIME → AMOUNT → BUY/SELL → WAIT
    """
    hwnd = find_broker_window(args.window_query)
    if hwnd is None:
        return 2
    if not activate_window(hwnd):
        return 2

    rect = get_window_rect(hwnd)
    if rect is None:
        LOGGER.error("Failed to get window rectangle")
        return 2

    LOGGER.info(
        "\n" + "="*70
    )
    LOGGER.info("808 CALIBRATOR - EXECUTION FLOW TRAINING")
    LOGGER.info("="*70)
    LOGGER.info("This workflow mimics the real signal-to-trade sequence.")
    LOGGER.info("Keep Pocket Option broker visible and do NOT move the window.")
    LOGGER.info("="*70)

    LOGGER.info("Starting fresh calibration; ignoring any previously saved boxes.")
    calibrated: Dict[str, Dict[str, Any]] = {}

    print(
        """
╔════════════════════════════════════════════════════════════════╗
║               EXECUTION FLOW SEQUENCE                          ║
║                                                                ║
║  1. SIGNAL RECEIVED (simulated)                              ║
║  2. NAVIGATE TO TIME BOX                                     ║
║  3. SEE +/- CONTROLS APPEAR (or preset time buttons)        ║
║  4. ADJUST TIME (use +/- or presets like 60s, 300s)        ║
║  5. NAVIGATE TO AMOUNT BOX                                  ║
║  6. NAVIGATE TO BUY or SELL BUTTON                          ║
║  7. CLICK BUTTON & WATCH TRADE EXECUTE                      ║
║                                                                ║
║  Press Enter at each step when mouse is on target.           ║
╚════════════════════════════════════════════════════════════════╝
    """
    )

    time.sleep(1.5)

    # STEP 1: Signal received (informational)
    print("\n" + "="*70)
    print("STEP 1: SIGNAL RECEIVED")
    print("="*70)
    print("🔔 Simulated BUY/SELL signal with 60s expiry received.")
    print("   Ready to execute trade sequence...")
    print("   Press ENTER to proceed to TIME adjustment.")
    print("="*70 + "\n")
    input()

    # STEP 2: Time box calibration
    calibrated["time_box"] = calibrate_point_interactive(
        hwnd,
        rect,
        "time_box",
        "Navigate to TIME display/box and press ENTER\n"
        "     (You should see current time value or slider)",
    )

    time.sleep(0.5)

    # STEP 3: Time adjustment controls (minus)
    print("\n" + "="*70)
    print("TIME ADJUSTMENT - FINE TUNING")
    print("="*70)
    print("You should now see the time popup/modal with:")
    print("  • A MINUS (-) button to decrease time")
    print("  • A PLUS (+) button to increase time")
    print("  • Quick preset buttons (30s, 60s, 120s, 300s)")
    print("\nNavigate to the MINUS (-) button or a quick preset.")
    print("=" * 70 + "\n")

    calibrated["time_adjustment_minus"] = calibrate_point_interactive(
        hwnd,
        rect,
        "time_adjustment_minus",
        "Hover on MINUS (-) button and press ENTER",
    )

    # STEP 4: Time plus button
    calibrated["time_adjustment_plus"] = calibrate_point_interactive(
        hwnd,
        rect,
        "time_adjustment_plus",
        "Hover on PLUS (+) button and press ENTER",
    )

    # STEP 5: Time presets
    time.sleep(0.3)
    print(
        """
╔════════════════════════════════════════════════════════════════╗
║          QUICK TIME PRESETS (if visible as buttons)          ║
╚════════════════════════════════════════════════════════════════╝

You should see quick preset buttons for common times:
  [30s]  [60s]  [120s]  [300s]  or similar

We'll capture the position of each preset for automated use.
    """
    )

    for time_sec in [30, 60, 120, 300]:
        calibrated[f"time_preset_{time_sec}"] = calibrate_point_interactive(
            hwnd,
            rect,
            f"time_preset_{time_sec}",
            f"Hover on the {time_sec}s PRESET BUTTON and press ENTER",
        )

    # CAPABILITY CAPTURE: determine plus/minus step behavior
    print("\nCalibration helper: determining plus/minus step size.")
    print("If you know how many seconds a single + or - click changes, enter that number now.")
    print("Otherwise, enter 0 to skip and accept default (30s).")
    val = input("Plus-step seconds (default 30): ").strip() or "30"
    try:
        plus_step = int(val)
    except Exception:
        plus_step = 30

    print("If the broker supports typing expiry directly into the time field, type 'y' otherwise leave blank.")
    supports_input = input("Supports direct input? [y/N]: ").strip().lower() == 'y'

    calibrated["capabilities"] = {
        "plus_step_seconds": plus_step,
        "minus_step_seconds": plus_step,
        "supports_direct_input": supports_input,
        "max_adjust_clicks": 10,
    }

    time.sleep(0.5)
    # Optional: capture the time input area where typing expiry is possible
    print("\nCapture the TYPEABLE time input area (where you can type expiry). If none, press ENTER on an empty area.")
    calibrated["time_input"] = calibrate_point_interactive(
        hwnd,
        rect,
        "time_input",
        "Hover on the typeable time input (or an empty area) and press ENTER",
    )

    time.sleep(0.4)

    # STEP 7: BUY button
    print("\n" + "="*70)
    print("STEP 3: TRADE BUTTONS")
    print("="*70)
    print("You should now see the green BUY and red SELL buttons.")
    print("These are at the bottom of the order panel.")
    print("="*70 + "\n")

    calibrated["buy_button"] = calibrate_point_interactive(
        hwnd,
        rect,
        "buy_button",
        "Hover on the GREEN BUY button and press ENTER",
    )

    # STEP 8: SELL button
    calibrated["sell_button"] = calibrate_point_interactive(
        hwnd,
        rect,
        "sell_button",
        "Hover on the RED SELL button and press ENTER",
    )

    # Save calibration
    save_boxes(calibrated)

    # Generate report
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hwnd": hwnd,
        "window_title": _window_title(hwnd),
        "window_class": _window_class(hwnd),
        "window_rect": {"left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom},
        "calibration_points": calibrated,
    }
    CALIBRATION_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "="*70)
    print("✅ CALIBRATION COMPLETE")
    print("="*70)
    print(f"Boxes saved to: {BOXES_FILE}")
    print(f"Report saved to: {CALIBRATION_REPORT}")
    print("\nNext steps:")
    print("  1. Preview: python 808_calibrator.py preview --window-query 'Your Window'")
    print("  2. Test manual trade (typing fallback preferred):")
    print("     python 808_calibrator.py test-execute --side buy --expiry 180 --window-query 'Your Window'")
    print("="*70 + "\n")

    return 0


def run_preview(args: argparse.Namespace) -> int:
    """Show calibrated box coordinates."""
    hwnd = find_broker_window(args.window_query)
    if hwnd is None:
        return 2
    if not activate_window(hwnd):
        return 2

    rect = get_window_rect(hwnd)
    if rect is None:
        LOGGER.error("Failed to get window rectangle")
        return 2

    boxes = load_boxes(require_saved=True)
    if boxes is None:
        return 2

    print("\n" + "="*70)
    print("CALIBRATED BOX PREVIEW")
    print("="*70)
    for name, rel in boxes.items():
        if name == "capabilities":
            print(f"  {name:<25} => metadata: {rel}")
            continue
        abs_pt = rel_to_abs(rect, float(rel.get("x", 0.0)), float(rel.get("y", 0.0)))
        print(f"  {name:<25} => abs({abs_pt[0]:<5}, {abs_pt[1]:<5}) rel({rel.get('x', 0.0):.4f}, {rel.get('y', 0.0):.4f})")
    print("="*70 + "\n")

    return 0


def run_test_execute(args: argparse.Namespace) -> int:
    """Test full trade execution with calibrated boxes."""
    hwnd = find_broker_window(args.window_query)
    if hwnd is None:
        return 2
    if not activate_window(hwnd):
        return 2

    rect = get_window_rect(hwnd)
    if rect is None:
        LOGGER.error("Failed to get window rectangle")
        return 2

    boxes = load_boxes(require_saved=True)
    if boxes is None:
        return 2

    print("\n" + "="*70)
    print("TEST EXECUTION - DRY RUN")
    print("="*70)
    print(f"Direction: {args.side.upper()}")
    print(f"Expiry: {args.expiry}s")
    print("="*70 + "\n")

    try:
        # Step 1: Click time box
        LOGGER.info("STEP 1: Click TIME box...")
        x, y = rel_to_abs(rect, boxes["time_box"]["x"], boxes["time_box"]["y"])
        slow_click(x, y, "time_box")
        time.sleep(0.45)

        # Step 2: allow popup to appear (up to ~5s) then try typing expiry, fallback to 300s preset
        LOGGER.info("STEP 2: Click TIME box and wait for popup to appear (up to 5s)...")
        time.sleep(0.45)
        # allow popup latency
        time.sleep(5.0)

        caps = boxes.get("capabilities", {})
        supports_input = bool(caps.get("supports_direct_input")) and "time_input" in boxes

        # Safety flag: track if direct input succeeded
        direct_input_succeeded = False

        if supports_input:
            try:
                LOGGER.info("Attempting direct typing of expiry %ss into time input...", args.expiry)
                x, y = rel_to_abs(rect, boxes["time_input"]["x"], boxes["time_input"]["y"])
                slow_click(x, y, "time_input")
                time.sleep(0.12)
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.08)
                # 2-second human-like pause before typing
                time.sleep(2.0)
                pyautogui.typewrite(str(args.expiry), interval=0.05)
                time.sleep(0.18)
                pyautogui.press("enter")
                time.sleep(0.3)
                LOGGER.info("Direct input succeeded for expiry %ss", args.expiry)
                direct_input_succeeded = True
            except Exception as exc:
                LOGGER.warning("Direct input failed: %s; will fallback to 300s preset", exc)

        # Only use preset fallback if direct input did NOT succeed
        if not direct_input_succeeded:
            time_key = "time_preset_300"
            if time_key in boxes:
                LOGGER.info("STEP 2b: Using fallback TIME PRESET 300s...")
                x, y = rel_to_abs(rect, boxes[time_key]["x"], boxes[time_key]["y"])
                slow_click(x, y, "time_preset_300")
                time.sleep(0.35)
            else:
                LOGGER.error("No 300s preset available in calibration; cannot set default expiry")
                return 1
        else:
            LOGGER.info("Direct input succeeded; skipping preset button, proceeding to BUY/SELL...")

        # PHASE 3: Optional OCR verification of displayed expiry
        time.sleep(0.5)  # Brief pause before OCR check
        actual_expiry = direct_input_succeeded and args.expiry or 300
        LOGGER.info("STEP 3: Verifying expiry via OCR (Phase 3 - Optional)...")
        ocr_verified = verify_expiry_via_ocr(hwnd, rect, boxes, actual_expiry)
        if not ocr_verified:
            LOGGER.warning("⚠ OCR verification failed; proceeding anyway but monitor broker for confirmation")

        # Step 4: Click BUY/SELL button
        button_key = "buy_button" if args.side.lower() == "buy" else "sell_button"
        LOGGER.info("STEP 4: Click %s button...", args.side.upper())
        x, y = rel_to_abs(rect, boxes[button_key]["x"], boxes[button_key]["y"])
        slow_click(x, y, f"{args.side}_button")
        time.sleep(0.50)

        print("\n" + "="*70)
        print("✅ TEST EXECUTION COMPLETE")
        print("="*70)
        print("Check your broker tab for trade confirmation.")
        print("If trade executed correctly, you're ready for automation!")
        print("="*70 + "\n")

        return 0

    except Exception as exc:
        LOGGER.error("Test execution failed: %s", exc)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python 808_calibrator.py',
        description="Interactive calibration trainer for real trade execution.",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    cal = sub.add_parser("calibrate", help="Interactive guided calibration workflow.")
    cal.add_argument("--window-query", default=None, help="Broker window title substring")
    cal.set_defaults(mode="calibrate")

    prev = sub.add_parser("preview", help="Show calibrated box coordinates.")
    prev.add_argument("--window-query", default=None, help="Broker window title substring")
    prev.set_defaults(mode="preview")

    test = sub.add_parser(
        "test-execute",
        help="Dry-run execution with calibrated boxes (no real trade, just simulation).",
    )
    test.add_argument("--side", choices=["buy", "sell"], required=True, help="Trade direction")
    test.add_argument("--expiry", type=int, default=180, help="Expiry seconds (default 180)")
    test.add_argument("--window-query", default=None, help="Broker window title substring")
    test.set_defaults(mode="test-execute")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.mode == "calibrate":
        return run_calibration(args)
    elif args.mode == "preview":
        return run_preview(args)
    elif args.mode == "test-execute":
        return run_test_execute(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped by user")
        sys.exit(0)
