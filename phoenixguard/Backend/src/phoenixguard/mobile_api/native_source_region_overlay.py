from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
from typing import Any, Sequence, cast

from PIL import Image, ImageEnhance, ImageTk


def _enable_per_monitor_dpi_awareness_v3() -> None:
    """Keep Win32 window pixels and the temporary Tk overlay in one space."""

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. A negative pointer value
        # is the documented Win32 pseudo-handle, not a process/window handle.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def normalize_drag_bbox_v3(
    start: Sequence[Any],
    end: Sequence[Any],
    *,
    width: int,
    height: int,
    minimum_pixels: int = 24,
) -> list[float]:
    canvas_width = max(1, int(width))
    canvas_height = max(1, int(height))
    x0 = max(0, min(canvas_width, int(round(float(start[0])))))
    y0 = max(0, min(canvas_height, int(round(float(start[1])))))
    x1 = max(0, min(canvas_width, int(round(float(end[0])))))
    y1 = max(0, min(canvas_height, int(round(float(end[1])))))
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right - left < int(minimum_pixels) or bottom - top < int(minimum_pixels):
        raise ValueError("Drag a larger chart region.")
    return [
        float(left) / float(canvas_width),
        float(top) / float(canvas_height),
        float(right) / float(canvas_width),
        float(bottom) / float(canvas_height),
    ]


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    user32 = ctypes.windll.user32
    if int(hwnd or 0) <= 0 or not bool(user32.IsWindow(int(hwnd))):
        return None
    rect = wintypes.RECT()
    if not bool(user32.GetWindowRect(int(hwnd), ctypes.byref(rect))):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _geometry(width: int, height: int, left: int, top: int) -> str:
    x_part = f"+{left}" if left >= 0 else str(left)
    y_part = f"+{top}" if top >= 0 else str(top)
    return f"{int(width)}x{int(height)}{x_part}{y_part}"


def _run_overlay(hwnd: int, frame_path: Path) -> dict[str, Any]:
    import tkinter as tk

    _enable_per_monitor_dpi_awareness_v3()
    target_rect = _window_rect(hwnd)
    if target_rect is None:
        return {"status": "error", "message": "The selected source window disappeared."}
    left, top, right, bottom = target_rect
    width = max(1, right - left)
    height = max(1, bottom - top)
    if width < 64 or height < 64:
        return {"status": "error", "message": "The selected source window is too small."}

    with Image.open(frame_path) as loaded:
        exact_wgc_frame = loaded.convert("RGB").copy()
    dimmed_frame = ImageEnhance.Brightness(exact_wgc_frame).enhance(0.72)

    result: dict[str, Any] = {
        "status": "cancelled",
        "message": "Windows region selection cancelled.",
    }
    finished = False
    start_point: tuple[int, int] | None = None
    selection_rectangle: int | None = None

    root = tk.Tk()
    root_any = cast(Any, root)
    root_any.overrideredirect(True)
    root_any.attributes("-topmost", True)
    root_any.geometry(_geometry(width, height, left, top))
    root_any.configure(bg="#080b0f")

    canvas = tk.Canvas(root, highlightthickness=0, cursor="crosshair", bg="#080b0f")
    canvas_any = cast(Any, canvas)
    canvas.pack(fill="both", expand=True)
    photo_holder: dict[str, Any] = {}

    def render_background(render_width: int, render_height: int) -> None:
        resized = dimmed_frame.resize(
            (max(1, int(render_width)), max(1, int(render_height))),
            Image.Resampling.BILINEAR,
        )
        photo = ImageTk.PhotoImage(resized)
        photo_holder["image"] = photo
        canvas.delete("source-background")
        canvas_any.create_image(0, 0, anchor="nw", image=photo, tags="source-background")
        canvas.tag_lower("source-background")

    render_background(width, height)
    canvas.create_rectangle(
        2,
        2,
        width - 2,
        height - 2,
        outline="#f2bd55",
        width=3,
        tags="selector-border",
    )
    canvas.create_rectangle(
        16,
        14,
        min(width - 16, 610),
        92,
        fill="#0a0d12",
        outline="#c9922d",
        width=1,
        tags="instructions",
    )
    canvas.create_text(
        30,
        27,
        anchor="nw",
        fill="#fff0c7",
        font=("Segoe UI", 12, "bold"),
        text=(
            "PhoenixGuard live source selector\n"
            "Drag the chart area. Release to lock and stream it. Right-click or Esc cancels."
        ),
        tags="instructions",
    )

    def finish(status: str, **updates: Any) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        result.update({"status": status, **updates})
        root_any.after_idle(root_any.destroy)

    def on_press(event: Any) -> None:
        nonlocal start_point, selection_rectangle
        start_point = (int(event.x), int(event.y))
        if selection_rectangle is not None:
            canvas.delete(selection_rectangle)
        selection_rectangle = canvas.create_rectangle(
            int(event.x),
            int(event.y),
            int(event.x),
            int(event.y),
            outline="#55efaa",
            fill="",
            width=3,
            dash=(7, 4),
        )

    def on_move(event: Any) -> None:
        if start_point is None or selection_rectangle is None:
            return
        canvas.coords(
            selection_rectangle,
            start_point[0],
            start_point[1],
            max(0, min(width, int(event.x))),
            max(0, min(height, int(event.y))),
        )

    def on_release(event: Any) -> None:
        if start_point is None:
            return
        try:
            normalized = normalize_drag_bbox_v3(
                start_point,
                (event.x, event.y),
                width=width,
                height=height,
            )
        except ValueError:
            return
        finish(
            "selected",
            normalized_bbox=normalized,
            source="native_ctrl_shift_b_wgc_frame",
            reference_frame_size=[int(exact_wgc_frame.width), int(exact_wgc_frame.height)],
        )

    def cancel(_event: Any | None = None) -> None:
        finish("cancelled", message="Windows region selection cancelled.")

    initial_size = (width, height)

    def poll_target() -> None:
        if finished:
            return
        current = _window_rect(hwnd)
        if current is None:
            finish("error", message="The selected source window disappeared during selection.")
            return
        current_left, current_top, current_right, current_bottom = current
        current_width = max(1, current_right - current_left)
        current_height = max(1, current_bottom - current_top)
        if (current_width, current_height) != initial_size:
            finish(
                "error",
                message="The source window was resized during selection. Press Ctrl+Shift+B and select it again.",
            )
            return
        if (current_left, current_top) != (left, top):
            root_any.geometry(_geometry(width, height, current_left, current_top))
        root_any.after(50, poll_target)

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_move)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<ButtonPress-3>", cancel)
    root_any.bind("<Escape>", cancel)
    root_any.after(50, poll_target)
    root_any.mainloop()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Select an exact region from a WGC source frame.")
    parser.add_argument("--hwnd", required=True, type=int)
    parser.add_argument("--frame-path", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = _run_overlay(int(args.hwnd), Path(args.frame_path))
    except Exception as exc:
        result = {"status": "error", "message": f"Windows region selection failed: {exc}"}
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
