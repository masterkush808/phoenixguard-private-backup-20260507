from __future__ import annotations

import argparse
import json
from typing import cast
from typing import Any, Sequence


def _normalize_focus_region_bbox(values: Sequence[Any]) -> list[float]:
    if len(values) < 4:
        return [0.0, 0.0, 1.0, 1.0]
    x0, y0, x1, y1 = [float(item) for item in values[:4]]
    x0 = max(0.0, min(1.0, x0))
    y0 = max(0.0, min(1.0, y0))
    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if (x1 - x0) <= 0.01:
        x0, x1 = 0.0, 1.0
    if (y1 - y0) <= 0.01:
        y0, y1 = 0.0, 1.0
    return [x0, y0, x1, y1]


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not bool(user32.GetWindowRect(int(hwnd), ctypes.byref(rect))):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _run_overlay(hwnd: int) -> dict[str, Any]:
    import tkinter as tk

    initial_rect = _window_rect(hwnd)
    if initial_rect is None:
        return {
            "status": "error",
            "message": "The Pocket Option window could not be located for focus selection.",
        }

    left, top, right, bottom = initial_rect
    width = max(1, right - left)
    height = max(1, bottom - top)
    start_point: tuple[int, int] | None = None
    rect_id: int | None = None
    selection_bbox: list[float] | None = None
    result: dict[str, Any] = {"status": "cancelled", "message": "Broker focus selection cancelled."}
    finished = False

    root = tk.Tk()
    root_any = cast(Any, root)
    root_any.overrideredirect(True)
    root_any.attributes("-topmost", True)
    try:
        root_any.attributes("-alpha", 0.22)
    except Exception:
        pass
    root_any.configure(bg="#0b0e12")
    root_any.geometry(f"{width}x{height}+{left}+{top}")

    canvas = tk.Canvas(root, bg="#0b0e12", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    canvas.create_rectangle(2, 2, width - 2, height - 2, outline="#d6a668", width=2)
    instruction_id = canvas.create_text(
        18,
        18,
        anchor="nw",
        fill="#f4ebdd",
        font=("Segoe UI", 11, "bold"),
        text="PhoenixGuard focus select\nDrag the chart area on Pocket Option\nPress Enter to confirm · Esc cancels",
    )

    def _finish(status: str, **updates: Any) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        result.update({"status": status, **updates})
        try:
            root_any.after_idle(root_any.destroy)
        except Exception:
            pass

    def _set_instruction(text: str, *, fill: str = "#f4ebdd") -> None:
        try:
            canvas.itemconfigure(instruction_id, text=text, fill=fill)
        except Exception:
            return

    def _on_press(event: Any) -> None:
        nonlocal start_point, rect_id, selection_bbox
        start_point = (
            max(0, min(width, int(event.x))),
            max(0, min(height, int(event.y))),
        )
        selection_bbox = None
        if rect_id is not None:
            canvas.delete(rect_id)
            rect_id = None
        _set_instruction(
            "PhoenixGuard focus select\nDrag the chart area on Pocket Option\nPress Enter to confirm · Esc cancels"
        )

    def _on_move(event: Any) -> None:
        nonlocal rect_id
        if start_point is None:
            return
        end_x = max(0, min(width, int(event.x)))
        end_y = max(0, min(height, int(event.y)))
        x0 = min(start_point[0], end_x)
        y0 = min(start_point[1], end_y)
        x1 = max(start_point[0], end_x)
        y1 = max(start_point[1], end_y)
        if rect_id is not None:
            canvas.delete(rect_id)
        rect_id = canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            outline="#f0d0a3",
            width=3,
            dash=(6, 4),
        )

    def _on_release(event: Any) -> None:
        nonlocal selection_bbox
        if start_point is None:
            return
        end_x = max(0, min(width, int(event.x)))
        end_y = max(0, min(height, int(event.y)))
        x0 = min(start_point[0], end_x)
        y0 = min(start_point[1], end_y)
        x1 = max(start_point[0], end_x)
        y1 = max(start_point[1], end_y)
        if (x1 - x0) < 18 or (y1 - y0) < 18:
            selection_bbox = None
            _set_instruction(
                "Selection too small\nDrag a larger Pocket Option chart region\nPress Enter to confirm · Esc cancels",
                fill="#ffb88c",
            )
            return
        selection_bbox = _normalize_focus_region_bbox(
            [
                float(x0) / max(width, 1),
                float(y0) / max(height, 1),
                float(x1) / max(width, 1),
                float(y1) / max(height, 1),
            ]
        )
        _set_instruction(
            "Selection ready\nPress Enter to lock this Pocket Option region\nDrag again to redraw · Esc cancels",
            fill="#9fe8ba",
        )

    def _on_confirm(_event: Any | None = None) -> None:
        if not selection_bbox:
            _set_instruction(
                "No region selected yet\nDrag the Pocket Option chart area first\nPress Enter to confirm · Esc cancels",
                fill="#ffcf8c",
            )
            return
        _finish(
            "selected",
            normalized_bbox=selection_bbox,
            source="native_ctrl_v_window",
        )

    def _on_escape(_event: Any | None = None) -> None:
        _finish("cancelled", message="Broker focus selection cancelled.")

    def _poll() -> None:
        nonlocal left, top, width, height
        if finished:
            return
        rect = _window_rect(hwnd)
        if rect is None:
            _finish(
                "error",
                message="The Pocket Option window disappeared before the focus selection completed.",
            )
            return
        current_left, current_top, current_right, current_bottom = rect
        current_width = max(1, current_right - current_left)
        current_height = max(1, current_bottom - current_top)
        if (
            current_left != left
            or current_top != top
            or current_width != width
            or current_height != height
        ):
            left, top, width, height = current_left, current_top, current_width, current_height
            root_any.geometry(f"{current_width}x{current_height}+{current_left}+{current_top}")
        root_any.after(40, _poll)

    canvas.bind("<ButtonPress-1>", _on_press)
    canvas.bind("<B1-Motion>", _on_move)
    canvas.bind("<ButtonRelease-1>", _on_release)
    canvas.bind("<ButtonPress-3>", _on_escape)
    root_any.bind("<Return>", _on_confirm)
    root_any.bind("<KP_Enter>", _on_confirm)
    root_any.bind("<Escape>", _on_escape)
    root_any.after(40, _poll)
    root_any.focus_force()
    root_any.lift()
    root_any.mainloop()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, required=True)
    args = parser.parse_args()

    result: dict[str, Any]
    try:
        result = _run_overlay(int(args.hwnd))
    except Exception as exc:
        result = {
            "status": "error",
            "message": f"Native broker focus selection failed: {exc}",
        }
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
