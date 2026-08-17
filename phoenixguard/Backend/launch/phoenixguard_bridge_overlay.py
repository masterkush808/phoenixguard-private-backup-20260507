#!/usr/bin/env python3
"""Floating always-on-top window that shows what the direct trade bridge sees.

The window has free movement (drag it anywhere), updates in real time from the
same PhoenixGuard observation state the bridge acts on, and only mirrors what
PhoenixGuard sees -- it never blocks or alters bridge decisions.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import tkinter as tk

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

import phoenixguard_direct_trade_bridge as bridge

_COLOR_MAP = {
    "green": "#7ee08a",
    "amber": "#e8c96a",
    "red": "#e07a7a",
    "cyan": "#7ad2e8",
    "white": "#d8e0ea",
    "dim": "#8fa6bf",
    "header": "#ffffff",
}


def _frame_lines(raw_lines: object) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    if not isinstance(raw_lines, Sequence) or isinstance(
        raw_lines, (str, bytes, bytearray)
    ):
        return lines
    for row in cast(Sequence[object], raw_lines):
        if not isinstance(row, Mapping):
            continue
        mapping = cast(Mapping[str, object], row)
        text = str(mapping.get("text") or "")
        color = str(mapping.get("color") or "dim")
        lines.append((text, _COLOR_MAP.get(color, "#d8e0ea")))
    return lines


class BridgeOverlay:
    def __init__(
        self,
        session_id: str,
        *,
        refresh_seconds: float,
        signal_source: str,
    ) -> None:
        self.session_id = session_id
        self.refresh_ms = max(0.25, float(refresh_seconds)) * 1000.0
        self.signal_source = signal_source
        self.drag_offset_x = 0
        self.drag_offset_y = 0

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)  # pyright: ignore[reportUnknownMemberType]
        self.root.attributes("-alpha", 0.94)  # pyright: ignore[reportUnknownMemberType]
        self.root.configure(bg="#101418")
        self.root.geometry("+40+40")

        self.frame = tk.Frame(
            self.root,
            bg="#101418",
            bd=1,
            relief="solid",
            highlightbackground="#2c3644",
            highlightthickness=1,
        )
        self.frame.pack(fill="both", expand=True)

        title_bar = tk.Frame(self.frame, bg="#18212b")
        title_bar.pack(fill="x")
        self.title_label = tk.Label(
            title_bar,
            text=f"Bridge view - {session_id}",
            bg="#18212b",
            fg="#8fa6bf",
            font=("Consolas", 9, "bold"),
            padx=6,
        )
        self.title_label.pack(side="left")
        close_button = tk.Label(
            title_bar,
            text="  x  ",
            bg="#18212b",
            fg="#e6b0b0",
            font=("Consolas", 9, "bold"),
            cursor="hand2",
        )
        close_button.pack(side="right")
        close_button.bind("<Button-1>", lambda _e: self.root.destroy())

        self.body = tk.Frame(self.frame, bg="#101418")
        self.body.pack(fill="both", expand=True, padx=8, pady=6)

        self.line_widgets: list[tk.Label] = []
        self.footer = tk.Label(
            self.frame,
            text="",
            bg="#101418",
            fg="#5a6b7e",
            font=("Consolas", 8),
            anchor="w",
            padx=8,
            pady=2,
        )
        self.footer.pack(fill="x", side="bottom")

        for widget in (
            self.title_label,
            close_button,
            self.body,
            self.footer,
            self.frame,
            self.root,
        ):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag_motion)

        self.last_payload_mtime = 0.0
        self.refresh()

    def _start_drag(self, event: tk.Event) -> None:
        self.drag_offset_x = int(event.x_root) - self.root.winfo_x()
        self.drag_offset_y = int(event.y_root) - self.root.winfo_y()

    def _drag_motion(self, event: tk.Event) -> None:
        x = int(event.x_root) - self.drag_offset_x
        y = int(event.y_root) - self.drag_offset_y
        self.root.geometry(f"+{x}+{y}")

    def _render(
        self,
        data: dict[str, Any],
        *,
        payload_mtime: float,
        payload_ok: bool,
    ) -> None:
        for widget in self.line_widgets:
            widget.destroy()
        self.line_widgets = []

        for text, color in _frame_lines(data.get("lines")):
            label = tk.Label(
                self.body,
                text=text,
                bg="#101418",
                fg=color,
                font=("Consolas", 9),
                anchor="w",
                justify="left",
            )
            label.pack(fill="x")
            self.line_widgets.append(label)

        age = 0.0 if payload_mtime <= 0.0 else max(0.0, time.time() - payload_mtime)
        if not payload_ok:
            state_text = "NO LIVE STATE - waiting for PhoenixGuard session"
            state_color = "#e07a7a"
        elif age > 30.0:
            state_text = f"stale payload {age:.0f}s old"
            state_color = "#e8c96a"
        else:
            state_text = f"up to date ({age:.1f}s old)"
            state_color = "#7ee08a"
        self.footer.configure(
            text=f"[{state_text}] updated {str(data.get('updated') or '')} | press Esc to close"
        )
        self.title_label.configure(fg=state_color)

    def refresh(self) -> None:
        payload, _payload_path, mtime = bridge.bridge_overlay_payload(
            self.session_id
        )
        payload_ok = bool(payload)
        data = bridge.bridge_overlay_frame(
            payload, signal_source=self.signal_source
        )
        self._render(
            cast(dict[str, Any], data),
            payload_mtime=mtime,
            payload_ok=payload_ok,
        )
        self.root.after(int(self.refresh_ms), self.refresh)

    def run(self) -> None:
        self.root.mainloop()


def _once_frame(session_id: str, *, signal_source: str) -> int:
    payload, payload_path, mtime = bridge.bridge_overlay_payload(session_id)
    data = bridge.bridge_overlay_frame(payload, signal_source=signal_source)
    print(__import__("json").dumps(data, sort_keys=True, ensure_ascii=True))
    if not payload:
        print(f"payload_path={payload_path} (not found or empty)", file=sys.stderr)
        return 1
    print(
        f"payload_path={payload_path} mtime_age={max(0.0, time.time() - mtime):.1f}s",
        file=sys.stderr,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Floating real-time view of what the direct trade bridge sees."
    )
    parser.add_argument("--session-id", default="")
    parser.add_argument("--refresh-seconds", type=float, default=1.0)
    parser.add_argument("--signal-source", default=bridge.DEFAULT_SIGNAL_SOURCE)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Render one frame to stdout and exit (no window).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    session_id = bridge.bridge_overlay_session_id(args.session_id)
    if args.once:
        return _once_frame(session_id, signal_source=args.signal_source)
    overlay = BridgeOverlay(
        session_id,
        refresh_seconds=float(args.refresh_seconds),
        signal_source=str(args.signal_source),
    )
    overlay.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
