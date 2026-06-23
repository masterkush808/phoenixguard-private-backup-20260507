from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from phoenixguard.execution.enter_now_monitor import (  # noqa: E402
    EnterNowPackage,
    extract_enter_now_packages,
    format_enter_now_notification,
)


try:
    import tkinter as tk
except Exception:  # pragma: no cover - exercised only on systems without tkinter
    tk = None  # type: ignore[assignment]


DEFAULT_SESSION_ID = "pocket-live-8788"
DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SETTINGS_PATH = REPO_ROOT / ".codex_runtime" / "enter_now_floating_gui_settings.json"
DEFAULT_LOG_PATH = REPO_ROOT / ".codex_runtime" / "enter_now_notifications.jsonl"
DEFAULT_WINDOW_SIZE = (430, 360)
MIN_WINDOW_SIZE = (340, 300)
MAX_WINDOW_SIZE = (900, 720)


class TrackerSnapshotClient:
    def __init__(self, *, base_url: str, session_id: str, timeout_sec: float = 1.5) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.session_id = str(session_id or DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID
        self.timeout_sec = max(0.25, float(timeout_sec or 1.5))

    def fetch_session(self) -> tuple[dict[str, Any], str]:
        errors: list[str] = []
        if self.base_url:
            try:
                return self._fetch_api_session(), "api"
            except Exception as exc:
                errors.append(f"api: {exc}")
        try:
            return self._fetch_direct_session(), "session_file"
        except Exception as exc:
            errors.append(f"file: {exc}")
        raise RuntimeError("; ".join(errors) or "tracker session unavailable")

    def _fetch_api_session(self) -> dict[str, Any]:
        session_q = urllib.parse.quote(self.session_id, safe="")
        return self._get_json(f"{self.base_url}/v1/mobile/window-tracker/sessions/{session_q}")

    def _get_json(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("response was not a JSON object")
        return dict(payload)

    def _fetch_direct_session(self) -> dict[str, Any]:
        path = self._direct_session_path()
        if not path.exists():
            raise FileNotFoundError(str(path))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"{path} did not contain a JSON object")
        return dict(payload)

    def _direct_session_path(self) -> Path:
        data_dir = REPO_ROOT / "data"
        try:
            from phoenixguard.core.config import RUNTIME

            data_dir = Path(RUNTIME.data_dir)
        except Exception:
            pass
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", self.session_id).strip("._").lower() or "session"
        return data_dir / "mobile_api" / "window_tracker" / "sessions" / slug / "session.json"


class EnterNowFloatingGui:
    def __init__(
        self,
        *,
        client: TrackerSnapshotClient,
        settings_path: Path,
        log_path: Path,
        poll_ms: int,
        max_age_sec: float,
        ignore_existing: bool,
        beep: bool,
        system_message: bool,
        system_message_seconds: int,
        opacity: float,
    ) -> None:
        self.client = client
        self.settings_path = settings_path
        self.log_path = log_path
        self.poll_seconds = max(0.25, float(poll_ms) / 1000.0)
        self.max_age_sec = max(1.0, float(max_age_sec))
        self.ignore_existing = bool(ignore_existing)
        self.beep = bool(beep)
        self.system_message = bool(system_message)
        self.system_message_seconds = max(5, int(system_message_seconds))
        self.opacity = max(0.55, min(1.0, float(opacity)))
        self.stop_event = threading.Event()
        self.ui_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.seen_keys: set[str] = set()
        self.first_scan = True
        self.packages: list[EnterNowPackage] = []
        self.events: list[dict[str, Any]] = []
        self.last_error = ""
        self.last_source = "waiting"
        self.last_poll_epoch = 0.0
        self.flash_until = 0.0
        self.drag_x = 0
        self.drag_y = 0
        self.resize_x = 0
        self.resize_y = 0
        self.resize_width = 0
        self.resize_height = 0
        self.position = (40, 120)
        self.size = DEFAULT_WINDOW_SIZE
        self.root: Any = None
        self.vars: dict[str, Any] = {}
        self.event_text: Any = None
        self.status_canvas: Any = None
        self.status_dot: Any = None
        self.side_label: Any = None
        self.status_label: Any = None
        self.reason_label: Any = None
        self.resize_grip: Any = None
        self._load_settings()

    def run(self) -> None:
        if tk is None:
            raise RuntimeError("tkinter is not available on this Python installation")
        self._build_window()
        thread = threading.Thread(target=self._poll_loop, name="enter-now-monitor-poller", daemon=True)
        thread.start()
        self.root.after(150, self._drain_queue)
        self.root.after(350, self._tick)
        self.root.mainloop()

    def _build_window(self) -> None:
        root = tk.Tk()
        self.root = root
        root.title("PhoenixGuard Enter Now Sentinel")
        root.geometry(f"{self.size[0]}x{self.size[1]}+{self.position[0]}+{self.position[1]}")
        root.configure(bg="#060A12")
        root.resizable(True, True)
        root.minsize(MIN_WINDOW_SIZE[0], MIN_WINDOW_SIZE[1])
        root.maxsize(MAX_WINDOW_SIZE[0], MAX_WINDOW_SIZE[1])
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        try:
            root.overrideredirect(True)
        except Exception:
            pass
        root.attributes("-topmost", True)
        root.attributes("-alpha", self.opacity)
        try:
            root.attributes("-toolwindow", True)
        except Exception:
            pass
        root.bind("<Alt-F4>", lambda _event: "break")
        root.bind("<Unmap>", lambda _event: root.after_idle(root.deiconify))

        bg = "#060A12"
        panel = "#0B1220"
        rail = "#142035"
        line = "#24334D"
        text = "#E8F2FF"
        muted = "#8EA4BF"
        cyan = "#22D3EE"
        amber = "#F59E0B"

        def var(name: str, value: str = "") -> Any:
            item = tk.StringVar(value=value)
            self.vars[name] = item
            return item

        for name in (
            "state",
            "subtitle",
            "side",
            "lane",
            "score",
            "packet",
            "reason",
            "source",
            "clock",
            "footer",
        ):
            var(name)

        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)

        header = tk.Frame(root, bg=bg, padx=12, pady=10)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        self.status_canvas = tk.Canvas(header, width=22, height=22, bg=bg, highlightthickness=0)
        self.status_canvas.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 9), pady=(2, 0))
        self.status_dot = self.status_canvas.create_oval(4, 4, 18, 18, fill=cyan, outline="#DDFBFF", width=1)

        title = tk.Label(
            header,
            text="ENTER NOW SENTINEL",
            bg=bg,
            fg=text,
            anchor="w",
            font=("Segoe UI", 11, "bold"),
        )
        title.grid(row=0, column=1, sticky="ew")
        subtitle = tk.Label(
            header,
            textvariable=self.vars["subtitle"],
            bg=bg,
            fg=muted,
            anchor="w",
            font=("Segoe UI", 8),
        )
        subtitle.grid(row=1, column=1, sticky="ew", pady=(1, 0))

        close_button = tk.Button(
            header,
            text="CLOSE",
            command=self._close,
            bg="#1B2538",
            fg="#F8FAFC",
            activebackground="#334155",
            activeforeground="#FFFFFF",
            relief="flat",
            padx=10,
            pady=4,
            font=("Segoe UI", 8, "bold"),
        )
        close_button.grid(row=0, column=2, rowspan=2, sticky="ne", padx=(10, 0))

        body = tk.Frame(root, bg=panel, highlightbackground=line, highlightthickness=1)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=0)
        body.grid_rowconfigure(3, weight=1)

        state_row = tk.Frame(body, bg=panel, padx=12, pady=10)
        state_row.grid(row=0, column=0, sticky="ew")
        state_row.grid_columnconfigure(0, weight=1)
        self.status_label = tk.Label(
            state_row,
            textvariable=self.vars["state"],
            bg=panel,
            fg=amber,
            anchor="w",
            font=("Segoe UI", 10, "bold"),
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        clock = tk.Label(
            state_row,
            textvariable=self.vars["clock"],
            bg=panel,
            fg="#A7F3D0",
            anchor="e",
            font=("Segoe UI", 8, "bold"),
        )
        clock.grid(row=0, column=1, sticky="e")

        hero = tk.Frame(body, bg="#08111F", padx=12, pady=12, highlightbackground="#1F2F49", highlightthickness=1)
        hero.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        hero.grid_columnconfigure(0, weight=1)
        self.side_label = tk.Label(
            hero,
            textvariable=self.vars["side"],
            bg="#08111F",
            fg="#E5EDF7",
            anchor="w",
            font=("Segoe UI", 26, "bold"),
        )
        self.side_label.grid(row=0, column=0, sticky="ew")
        lane = tk.Label(
            hero,
            textvariable=self.vars["lane"],
            bg="#08111F",
            fg="#DDE8FF",
            anchor="w",
            font=("Segoe UI", 10, "bold"),
        )
        lane.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        score = tk.Label(
            hero,
            textvariable=self.vars["score"],
            bg="#08111F",
            fg="#A7F3D0",
            anchor="w",
            font=("Segoe UI", 9),
        )
        score.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        packet = tk.Label(
            hero,
            textvariable=self.vars["packet"],
            bg="#08111F",
            fg=muted,
            anchor="w",
            font=("Consolas", 8),
        )
        packet.grid(row=3, column=0, sticky="ew", pady=(5, 0))

        reason = tk.Label(
            body,
            textvariable=self.vars["reason"],
            bg=panel,
            fg="#FDE68A",
            anchor="w",
            justify="left",
            wraplength=380,
            padx=12,
            font=("Segoe UI", 8, "bold"),
        )
        self.reason_label = reason
        reason.grid(row=2, column=0, sticky="new", padx=0, pady=(0, 8))

        feed_frame = tk.Frame(body, bg=rail, padx=8, pady=8)
        feed_frame.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        feed_frame.grid_columnconfigure(0, weight=1)
        feed_frame.grid_rowconfigure(0, weight=1)
        self.event_text = tk.Text(
            feed_frame,
            bg=rail,
            fg="#BFD7FF",
            insertbackground="#BFD7FF",
            relief="flat",
            wrap="word",
            height=5,
            font=("Consolas", 8),
        )
        self.event_text.grid(row=0, column=0, sticky="nsew")
        self.event_text.configure(state="disabled")

        footer = tk.Frame(root, bg=bg, padx=12, pady=0)
        footer.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        footer.grid_columnconfigure(0, weight=1)
        source = tk.Label(
            footer,
            textvariable=self.vars["source"],
            bg=bg,
            fg=muted,
            anchor="w",
            font=("Segoe UI", 7),
        )
        source.grid(row=0, column=0, sticky="ew")
        footer_text = tk.Label(
            footer,
            textvariable=self.vars["footer"],
            bg=bg,
            fg="#64748B",
            anchor="e",
            font=("Segoe UI", 7),
        )
        footer_text.grid(row=0, column=1, sticky="e")
        self.resize_grip = tk.Canvas(footer, width=22, height=18, bg=bg, highlightthickness=0, cursor="sizing")
        self.resize_grip.grid(row=0, column=2, sticky="e", padx=(8, 0))
        for offset in (0, 5, 10):
            self.resize_grip.create_line(
                20 - offset,
                17,
                21,
                16 - offset,
                fill="#64748B",
                width=2,
            )
        self.resize_grip.bind("<ButtonPress-1>", self._on_resize_press)
        self.resize_grip.bind("<B1-Motion>", self._on_resize_motion)
        self.resize_grip.bind("<ButtonRelease-1>", self._on_resize_release)

        self._bind_motion(root, header, title, subtitle, body, state_row, hero, lane, score, packet, reason, feed_frame)
        self._build_menu(root)
        self._apply_layout_size()
        self._render()

    def _bind_motion(self, *widgets: Any) -> None:
        for widget in widgets:
            try:
                widget.bind("<ButtonPress-1>", self._on_press)
                widget.bind("<B1-Motion>", self._on_motion)
                widget.bind("<ButtonRelease-1>", self._on_release)
                widget.bind("<Button-3>", self._show_menu)
            except Exception:
                pass

    def _build_menu(self, root: Any) -> None:
        menu = tk.Menu(root, tearoff=False, bg="#0B1220", fg="#E8F2FF")
        menu.add_command(label="Snap Top Right", command=lambda: self._snap("top_right"))
        menu.add_command(label="Snap Bottom Right", command=lambda: self._snap("bottom_right"))
        menu.add_separator()
        menu.add_command(label="Compact Size", command=lambda: self._set_window_size(*DEFAULT_WINDOW_SIZE))
        menu.add_command(label="Tall Size", command=lambda: self._set_window_size(430, 520))
        menu.add_command(label="Wide Size", command=lambda: self._set_window_size(640, 420))
        menu.add_command(label="Command Size", command=lambda: self._set_window_size(760, 560))
        menu.add_separator()
        menu.add_command(label="Opacity +", command=lambda: self._set_opacity(self.opacity + 0.05))
        menu.add_command(label="Opacity -", command=lambda: self._set_opacity(self.opacity - 0.05))
        menu.add_separator()
        menu.add_command(label="Close", command=self._close)
        self.menu = menu

    def _show_menu(self, event: Any) -> None:
        try:
            self.menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            try:
                self.menu.grab_release()
            except Exception:
                pass

    def _on_press(self, event: Any) -> None:
        self.drag_x = int(event.x_root)
        self.drag_y = int(event.y_root)

    def _on_motion(self, event: Any) -> None:
        root = self.root
        if root is None:
            return
        dx = int(event.x_root) - self.drag_x
        dy = int(event.y_root) - self.drag_y
        root.geometry(f"+{root.winfo_x() + dx}+{root.winfo_y() + dy}")
        self.drag_x = int(event.x_root)
        self.drag_y = int(event.y_root)

    def _on_release(self, _event: Any) -> None:
        self._apply_layout_size()
        self._save_settings()

    def _on_resize_press(self, event: Any) -> str:
        root = self.root
        if root is None:
            return "break"
        self.resize_x = int(event.x_root)
        self.resize_y = int(event.y_root)
        self.resize_width = int(root.winfo_width())
        self.resize_height = int(root.winfo_height())
        return "break"

    def _on_resize_motion(self, event: Any) -> str:
        root = self.root
        if root is None:
            return "break"
        next_width = self.resize_width + int(event.x_root) - self.resize_x
        next_height = self.resize_height + int(event.y_root) - self.resize_y
        width, height = self._clamp_size(next_width, next_height)
        root.geometry(f"{width}x{height}+{root.winfo_x()}+{root.winfo_y()}")
        self.size = (width, height)
        self._apply_layout_size()
        return "break"

    def _on_resize_release(self, _event: Any) -> str:
        self._apply_layout_size()
        self._save_settings()
        return "break"

    def _clamp_size(self, width: int, height: int) -> tuple[int, int]:
        return (
            max(MIN_WINDOW_SIZE[0], min(MAX_WINDOW_SIZE[0], int(width))),
            max(MIN_WINDOW_SIZE[1], min(MAX_WINDOW_SIZE[1], int(height))),
        )

    def _set_window_size(self, width: int, height: int) -> None:
        root = self.root
        width, height = self._clamp_size(width, height)
        self.size = (width, height)
        if root is not None:
            root.geometry(f"{width}x{height}+{root.winfo_x()}+{root.winfo_y()}")
        self._apply_layout_size()
        self._save_settings()

    def _apply_layout_size(self) -> None:
        root = self.root
        if root is None:
            return
        width, height = self._clamp_size(int(root.winfo_width()), int(root.winfo_height()))
        self.size = (width, height)
        wrap = max(230, width - 56)
        if self.reason_label is not None:
            try:
                self.reason_label.configure(wraplength=wrap)
            except Exception:
                pass
        if self.event_text is not None:
            try:
                self.event_text.configure(height=max(4, min(14, int((height - 260) / 18))))
            except Exception:
                pass

    def _snap(self, corner: str) -> None:
        root = self.root
        if root is None:
            return
        width, height = self.size
        screen_w = int(root.winfo_screenwidth())
        screen_h = int(root.winfo_screenheight())
        margin = 18
        x = max(0, screen_w - width - margin)
        y = margin if corner == "top_right" else max(0, screen_h - height - margin)
        root.geometry(f"{width}x{height}+{x}+{y}")
        self._apply_layout_size()
        self._save_settings()

    def _set_opacity(self, value: float) -> None:
        self.opacity = max(0.55, min(1.0, float(value)))
        if self.root is not None:
            self.root.attributes("-alpha", self.opacity)
        self._save_settings()

    def _close(self) -> None:
        self.stop_event.set()
        self._save_settings()
        if self.root is not None:
            self.root.destroy()

    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                payload, source = self.client.fetch_session()
                now = time.time()
                packages = extract_enter_now_packages(payload, now_epoch=now, max_age_sec=self.max_age_sec)
                fresh_packages = [item for item in packages if item.is_fresh(now_epoch=now, max_age_sec=self.max_age_sec)]
                notifications: list[EnterNowPackage] = []
                if self.first_scan and self.ignore_existing:
                    self.seen_keys.update(item.key for item in fresh_packages)
                else:
                    for package in fresh_packages:
                        if package.key in self.seen_keys:
                            continue
                        self.seen_keys.add(package.key)
                        notifications.append(package)
                self.first_scan = False
                for package in notifications:
                    self._notify(package)
                self.ui_queue.put(
                    {
                        "type": "snapshot",
                        "packages": packages,
                        "source": source,
                        "error": "",
                        "notifications": [item.as_dict() for item in notifications],
                    }
                )
            except Exception as exc:
                self.ui_queue.put({"type": "error", "error": str(exc)})
            self.stop_event.wait(self.poll_seconds)

    def _notify(self, package: EnterNowPackage) -> None:
        message = format_enter_now_notification(package)
        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": message,
            "package": package.as_dict(),
        }
        self._append_event(record)
        self._write_log(record)
        if self.beep:
            self._beep()
        if self.system_message:
            self._send_system_message(message)

    def _append_event(self, event: dict[str, Any]) -> None:
        self.events.insert(0, event)
        del self.events[24:]

    def _write_log(self, record: Mapping[str, Any]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, default=str, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def _beep(self) -> None:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            winsound.Beep(1320, 180)
            winsound.Beep(990, 140)
        except Exception:
            try:
                if self.root is not None:
                    self.root.bell()
            except Exception:
                pass

    def _send_system_message(self, message: str) -> None:
        if os.name != "nt":
            return
        username = os.environ.get("USERNAME")
        if not username:
            return
        try:
            subprocess.run(
                ["msg", username, f"/time:{self.system_message_seconds}", message],
                check=False,
                timeout=2.0,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _drain_queue(self) -> None:
        try:
            while True:
                item = self.ui_queue.get_nowait()
                if item.get("type") == "snapshot":
                    self.packages = list(item.get("packages") or [])
                    self.last_source = str(item.get("source") or "tracker")
                    self.last_error = ""
                    self.last_poll_epoch = time.time()
                    if item.get("notifications"):
                        self.flash_until = time.time() + 9.0
                elif item.get("type") == "error":
                    self.last_error = str(item.get("error") or "")
                    self.last_poll_epoch = time.time()
        except queue.Empty:
            pass
        self._render()
        if self.root is not None and not self.stop_event.is_set():
            self.root.after(150, self._drain_queue)

    def _tick(self) -> None:
        self._render()
        if self.root is not None and not self.stop_event.is_set():
            self.root.after(350, self._tick)

    def _render(self) -> None:
        now = time.time()
        clock = time.strftime("%H:%M:%S")
        fresh = [item for item in self.packages if item.is_fresh(now_epoch=now, max_age_sec=self.max_age_sec)]
        current = fresh[0] if fresh else (self.packages[0] if self.packages else None)
        if current is None:
            state = "WAITING FOR ENTER NOW"
            subtitle = "Monitoring current tracker for Enter Now packages only"
            side = "NO PACKAGE"
            lane = "Tracker has not published an Enter Now package yet"
            score = "Score pending"
            packet = f"session={self.client.session_id}"
            reason = self.last_error or "The GUI is armed and watching."
            dot = "#22D3EE" if not self.last_error else "#EF4444"
            side_color = "#E5EDF7"
            state_color = "#22D3EE" if not self.last_error else "#EF4444"
        else:
            blocked = current.blocked
            state = "BLOCKED ENTER NOW PACKAGE" if blocked else "ENTER NOW PACKAGE"
            if current not in fresh:
                state = f"EXPIRED {state}"
            subtitle = "Runtime-held signal detected" if blocked else "Executable Enter Now signal detected"
            side = current.side or "HOLD"
            lane = current.lane or "LANE_PENDING"
            score = self._score_line(current)
            packet = f"packet={current.packet_id or 'n/a'} | type={current.packet_type} | source={current.source}"
            reason = current.blocker or current.broker_message or current.broker_status or "No blocker text published"
            dot = "#F59E0B" if blocked else "#22C55E"
            if current not in fresh:
                dot = "#64748B"
            side_color = "#2FCE65" if side == "BUY" else "#FF4B42" if side == "SELL" else "#E5EDF7"
            state_color = "#F59E0B" if blocked else "#22C55E"

        if now < self.flash_until and int(now * 4) % 2 == 0:
            dot = "#FFFFFF"
            state_color = "#FFFFFF"

        self._set_var("state", state)
        self._set_var("subtitle", subtitle)
        self._set_var("side", side)
        self._set_var("lane", lane)
        self._set_var("score", score)
        self._set_var("packet", packet)
        self._set_var("reason", reason[:220])
        self._set_var("source", self._source_line())
        self._set_var("clock", clock)
        self._set_var("footer", f"{self.size[0]}x{self.size[1]}")
        try:
            if self.status_canvas is not None and self.status_dot is not None:
                self.status_canvas.itemconfigure(self.status_dot, fill=dot)
            if self.side_label is not None:
                self.side_label.configure(fg=side_color)
            if self.status_label is not None:
                self.status_label.configure(fg=state_color)
        except Exception:
            pass
        self._apply_layout_size()
        self._render_events()

    def _render_events(self) -> None:
        if self.event_text is None:
            return
        if self.events:
            lines = []
            for event in self.events[:8]:
                at = str(event.get("at") or "")[11:19] or time.strftime("%H:%M:%S")
                lines.append(f"{at}  {event.get('message', '')}")
            text = "\n".join(lines)
        elif self.last_error:
            text = f"{time.strftime('%H:%M:%S')}  Tracker unavailable: {self.last_error}"
        else:
            text = f"{time.strftime('%H:%M:%S')}  Watching for fresh Enter Now packages."
        try:
            self.event_text.configure(state="normal")
            self.event_text.delete("1.0", "end")
            self.event_text.insert("1.0", text)
            self.event_text.configure(state="disabled")
        except Exception:
            pass

    def _score_line(self, package: EnterNowPackage) -> str:
        if package.final_score is None:
            return "Score pending"
        if package.threshold is None:
            return f"Council score {package.final_score:.2f}"
        gap = package.final_score - package.threshold
        return f"Council score {package.final_score:.2f} / {package.threshold:.2f} | gap {gap:+.2f}"

    def _source_line(self) -> str:
        age = "never"
        if self.last_poll_epoch > 0.0:
            age = f"{max(0.0, time.time() - self.last_poll_epoch):.1f}s ago"
        if self.last_error:
            return f"source=unavailable | last poll {age} | {self.last_error[:90]}"
        return f"source={self.last_source} | session={self.client.session_id} | last poll {age}"

    def _set_var(self, name: str, value: str) -> None:
        item = self.vars.get(name)
        if item is not None:
            item.set(str(value))

    def _load_settings(self) -> None:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, Mapping):
                return
            x = int(data.get("x", self.position[0]))
            y = int(data.get("y", self.position[1]))
            width = int(data.get("width", self.size[0]))
            height = int(data.get("height", self.size[1]))
            self.position = (max(0, x), max(0, y))
            self.size = self._clamp_size(width, height)
            self.opacity = max(0.55, min(1.0, float(data.get("opacity", self.opacity))))
        except Exception:
            pass

    def _save_settings(self) -> None:
        try:
            root = self.root
            payload = {
                "height": self.size[1],
                "opacity": self.opacity,
                "width": self.size[0],
                "x": self.position[0],
                "y": self.position[1],
            }
            if root is not None:
                payload["x"] = int(root.winfo_x())
                payload["y"] = int(root.winfo_y())
                payload["width"] = int(root.winfo_width())
                payload["height"] = int(root.winfo_height())
                self.position = (payload["x"], payload["y"])
                self.size = self._clamp_size(payload["width"], payload["height"])
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Floating PhoenixGuard Enter Now package notifier.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("PHOENIXGUARD_ENTER_NOW_BASE_URL")
        or os.getenv("PHOENIXGUARD_MOBILE_API_BASE_URL")
        or DEFAULT_BASE_URL,
        help="Mobile API base URL. The GUI falls back to the persisted session file if this is unavailable.",
    )
    parser.add_argument(
        "--session-id",
        default=os.getenv("PHOENIXGUARD_ENTER_NOW_SESSION_ID") or DEFAULT_SESSION_ID,
        help="Window tracker session id to monitor.",
    )
    parser.add_argument("--poll-ms", type=int, default=1000, help="Polling interval in milliseconds.")
    parser.add_argument("--timeout-sec", type=float, default=1.5, help="HTTP timeout for tracker polling.")
    parser.add_argument("--max-age-sec", type=float, default=900.0, help="Maximum package age that can trigger a notification.")
    parser.add_argument("--ignore-existing", action="store_true", help="Show existing packages without notifying on startup.")
    parser.add_argument("--no-beep", action="store_true", help="Disable audible alert.")
    parser.add_argument("--no-system-message", action="store_true", help="Disable Windows msg notification.")
    parser.add_argument("--system-message-seconds", type=int, default=45, help="Seconds to keep Windows msg visible.")
    parser.add_argument("--opacity", type=float, default=0.95, help="Floating window opacity.")
    parser.add_argument("--settings-path", type=Path, default=DEFAULT_SETTINGS_PATH)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = TrackerSnapshotClient(base_url=args.base_url, session_id=args.session_id, timeout_sec=args.timeout_sec)
    gui = EnterNowFloatingGui(
        client=client,
        settings_path=args.settings_path,
        log_path=args.log_path,
        poll_ms=args.poll_ms,
        max_age_sec=args.max_age_sec,
        ignore_existing=args.ignore_existing,
        beep=not bool(args.no_beep),
        system_message=not bool(args.no_system_message),
        system_message_seconds=args.system_message_seconds,
        opacity=args.opacity,
    )
    gui.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
