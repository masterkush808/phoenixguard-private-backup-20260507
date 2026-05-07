#!/usr/bin/env python3
# pyright: reportUnusedFunction=false, reportUnusedVariable=false, reportGeneralTypeIssues=false
"""
808 SHOOTER - Standalone Pocket Option live click executor.

This script is isolated from the main execution path but can still consume
signals from the PhoenixGuard architecture via the observer endpoint:
GET /v1/mobile/observer/sessions/{sessionId}/signals/latest

Modes:
- Manual one-shot: BUY/SELL + expiry
- Signal-follow loop: poll architecture and execute actionable BUY/SELL

It performs real UI clicks on the Pocket Option order panel.
"""

import argparse
import ctypes
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from ctypes import Structure, WINFUNCTYPE, byref, c_bool, c_int
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

import pyautogui

try:
    import tkinter as tk
    from tkinter import ttk
    has_tkinter = True
except Exception:
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    has_tkinter = False

keyboard: Any | None = None
pytesseract: Any | None = None

try:
    import keyboard  # type: ignore[reportMissingModuleSource]
    has_keyboard = True
except Exception:
    has_keyboard = False

try:
    import pytesseract  # type: ignore[reportMissingTypeStubs]
    has_ocr = True
except Exception:
    has_ocr = False

# Disable PyAutoGUI failsafe corner abort for smoother automation.
pyautogui.FAILSAFE = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("808_shooter")

BOXES_FILE = Path("808_shooter_boxes.json")
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
COMMON_LOCAL_BASE_PORTS = (8793, 8787, 8000)
DEFAULT_BROKER_URL = "https://pocketoption.com/"
DEFAULT_BROKER_OPEN_TIMEOUT = 18.0
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_B = ord("B")

# Global state for automatic trigger toggle via Ctrl+B hotkey
automatic_trigger_enabled = True
automatic_trigger_lock = threading.Lock()
last_hotkey_toggle_ts = 0.0  # Debounce timestamp (prevent rapid Ctrl+B toggles)
HOTKEY_DEBOUNCE_SECONDS = 0.5
SAFETY_PAUSE_MIN_SECONDS = 1
SAFETY_PAUSE_MAX_SECONDS = 12

# Expiry-stability guard: require 2 consecutive polls with same (signal_id, expiry_seconds)
last_stable_signal: Dict[str, int | str] = {
    "signal_id": "",
    "expiry_seconds": 0,
    "polls_matched": 0,  # Count of consecutive polls with identical signal_id+expiry
}
stable_signal_lock = threading.Lock()

# Adaptive timing defaults and limits
DEFAULT_MAX_ADJUST_CLICKS = 10
DEFAULT_PLUS_STEP_SECONDS = 30
DEFAULT_MINUS_STEP_SECONDS = 30
DEFAULT_MIN_EXPIRY = 1
DEFAULT_MAX_EXPIRY = 3600 * 4  # 4 hours hard cap
DEFAULT_EXPIRY_FALLBACK_SECONDS = 180
DEFAULT_EXPIRY_ROUNDING_SECONDS = 5
DEFAULT_TRACKER_STUDY_INTERVAL_SEC = 3.0
DEFAULT_TRACKER_MIN_STUDY_INTERVAL_SEC = 0.5
DEFAULT_TRACKER_MAX_STUDY_INTERVAL_SEC = 10.0

# Enforce strict execution behavior: when True, do not use implicit fallbacks
# for missing expiry or kernel/hold-trigger fallbacks. This makes the shooter
# refuse to act on signals that lack explicit actionable fields so the system
# must be fully tightened and calibrated before deployment.
ENFORCE_STRICT_EXECUTION = True
AUTHORITATIVE_SIGNAL_ENDPOINT = "tracker"

# Test signal configuration
TEST_SIGNAL_EXPIRY_SECONDS = 30
TEST_SIGNAL_TIMEOUT_SECONDS = 30
TEST_SIGNAL_POLL_INTERVAL = 0.5

_BUY_SIDE_ALIASES = {"BUY", "CALL", "UP", "LONG", "BULL", "BULLISH"}
_SELL_SIDE_ALIASES = {"SELL", "PUT", "DOWN", "SHORT", "BEAR", "BEARISH"}
_MISSING_TEXT_VALUES = {"", "N/A", "NA", "NONE", "NULL", "NIL", "--", "UNKNOWN"}
_EMPTY_SENTINEL = object()
_TRACKER_SIGNAL_CONTEXT_KEYS = (
    "session_id",
    "market",
    "window_query",
    "capture_interval_sec",
    "effective_capture_interval_sec",
    "next_capture_in_sec",
    "next_study",
    "next_study_seconds",
    "next_study_in_sec",
    "next_study_countdown",
    "study_countdown",
    "study_interval_sec",
    "adaptive_timer_reason",
    "countdown_seconds",
    "next_event_countdown",
    "countdown_to_inference",
    "tracking_summary",
    "execution_controls",
    "broker_surface",
    "broker_execution_state",
)


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return len(value) == 0  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, tuple):
        return len(value) == 0  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, dict):
        return len(value) == 0  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, set):
        return len(value) == 0  # pyright: ignore[reportUnknownArgumentType]
    if isinstance(value, str):
        return value.strip().upper() in _MISSING_TEXT_VALUES
    return False


def _truncate_text(value: Any, max_length: int = 72) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3] + "..."


def _coerce_nonnegative_seconds(raw: Any) -> Optional[float]:
    if isinstance(raw, (int, float)):
        return max(0.0, float(raw))
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return max(0.0, float(text))
        except Exception:
            match = re.search(r"\d+(?:\.\d+)?", text)
            if match:
                return max(0.0, float(match.group(0)))
    return None


def _format_seconds(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))}s"
    return f"{value:.1f}s"


def _parse_expiry_seconds_value(raw: Any) -> Optional[int]:
    # Accept int/float values directly.
    if isinstance(raw, (int, float)):
        if float(raw) <= 0:
            return None
        return int(round(float(raw)))

    # Accept strings like "160", "160.0", "2:40", "00:02:40".
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if ":" in s:
            parts = [p.strip() for p in s.split(":") if p.strip()]
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                mm, ss = int(parts[0]), int(parts[1])
                return max(1, mm * 60 + ss)
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                hh, mm, ss = int(parts[0]), int(parts[1]), int(parts[2])
                return max(1, hh * 3600 + mm * 60 + ss)

        m = re.search(r"\d+(?:\.\d+)?", s)
        if m:
            return int(round(float(m.group(0))))
    return None


def _execution_expiry_field_candidates(payload: Dict[str, Any]) -> List[Tuple[str, Any]]:
    broker_state = payload.get("broker_execution_state")
    broker_dict: Dict[str, Any] = cast(Dict[str, Any], broker_state) if isinstance(broker_state, dict) else {}
    return [
        ("expiry_seconds", payload.get("expiry_seconds")),
        ("expiry_sec", payload.get("expiry_sec")),
        ("required_seconds", payload.get("required_seconds")),
        ("demo_execution_expiry_seconds", payload.get("demo_execution_expiry_seconds")),
        ("broker_execution_state.expiry_seconds", broker_dict.get("expiry_seconds")),
        ("expiry_text", payload.get("expiry_text")),
        ("hold_seconds", payload.get("hold_seconds")),
    ]


def _expiry_field_candidates(payload: Dict[str, Any]) -> List[Tuple[str, Any]]:
    return [
        *_execution_expiry_field_candidates(payload),
        ("countdown_seconds", payload.get("countdown_seconds")),
        ("next_event_countdown", payload.get("next_event_countdown")),
        ("countdown_to_inference", payload.get("countdown_to_inference")),
        ("next_capture_in_sec", payload.get("next_capture_in_sec")),
        ("next_study_seconds", payload.get("next_study_seconds")),
        ("next_study_in_sec", payload.get("next_study_in_sec")),
        ("effective_capture_interval_sec", payload.get("effective_capture_interval_sec")),
        ("capture_interval_sec", payload.get("capture_interval_sec")),
    ]


def _resolve_expiry_raw(payload: Dict[str, Any]) -> Tuple[Optional[int], str, Any]:
    for field_name, raw in _expiry_field_candidates(payload):
        parsed = _parse_expiry_seconds_value(raw)
        if parsed is not None:
            return parsed, field_name, raw
    return None, "n/a", None


def _resolve_display_side(payload: Dict[str, Any]) -> Tuple[str, str]:
    decision_kernel = payload.get("decision_kernel")
    kernel_dict: Dict[str, Any] = cast(Dict[str, Any], decision_kernel) if isinstance(decision_kernel, dict) else {}
    resolved_candidates: List[Tuple[str, Any]] = [
        ("scenario_top_direction", payload.get("scenario_top_direction")),
        ("tracking_summary.global_direction", payload.get("major_bias")),
        ("tracking_summary.local_direction", payload.get("bias_direction")),
        ("current_thesis", payload.get("current_thesis")),
        ("decision_kernel.dominant_side", kernel_dict.get("dominant_side")),
        ("decision_kernel.next_candle_bias", kernel_dict.get("next_candle_bias")),
        ("execution_action", payload.get("execution_action")),
        ("action", payload.get("action")),
        ("side", payload.get("side")),
    ]

    for source_name, candidate in resolved_candidates:
        side = _normalize_trade_side(candidate)
        if side in {"BUY", "SELL"}:
            return side, source_name

    thesis_text = str(payload.get("current_thesis", "") or payload.get("thesis_text", "") or "").upper()
    if "SELL" in thesis_text and "BUY" not in thesis_text:
        return "SELL", "current_thesis"
    if "BUY" in thesis_text and "SELL" not in thesis_text:
        return "BUY", "current_thesis"
    return "HOLD", "n/a"


def _resolve_thesis_text(payload: Dict[str, Any]) -> str:
    scenario_analysis = payload.get("scenario_analysis")
    if isinstance(scenario_analysis, dict):
        scenario_dict = cast(Dict[str, Any], scenario_analysis)
        top_scenario_any = scenario_dict.get("top_scenario")
        if isinstance(top_scenario_any, dict):
            top_scenario = cast(Dict[str, Any], top_scenario_any)
            direction = _normalize_trade_side(top_scenario.get("direction"))
            transition_type = str(top_scenario.get("transition_type", "") or "").strip().lower()
            if direction in {"BUY", "SELL"}:
                if transition_type == "continue":
                    return f"CONTINUATION {direction}"
                if transition_type:
                    return f"{transition_type.replace('_', ' ').upper()} {direction}"
                return direction
            text = _truncate_text(top_scenario.get("name") or top_scenario.get("label") or top_scenario.get("description"), 72)
            if text and text != "n/a":
                return text

    candidates = (
        payload.get("current_thesis"),
        payload.get("thesis_text"),
        payload.get("thesis_action"),
        payload.get("scenario_thesis"),
        payload.get("signal_thesis"),
    )
    for candidate in candidates:
        text = _truncate_text(candidate, 72)
        if text and text != "n/a":
            return text
    return "n/a"


def _resolve_next_study_seconds(payload: Dict[str, Any]) -> Optional[float]:
    for key in (
        "next_study_seconds",
        "next_study_in_sec",
        "next_study_countdown",
        "next_study",
        "next_capture_in_sec",
        "effective_capture_interval_sec",
        "capture_interval_sec",
        "next_event_countdown",
        "countdown_seconds",
        "countdown_to_inference",
    ):
        value = _coerce_nonnegative_seconds(payload.get(key))
        if value is not None:
            return min(DEFAULT_TRACKER_MAX_STUDY_INTERVAL_SEC, max(0.0, float(value)))
    return DEFAULT_TRACKER_STUDY_INTERVAL_SEC


def fetch_tracker_session_snapshot(base_url: str, session_id: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        url = f"{base_url.rstrip('/')}/v1/mobile/window-tracker/sessions/{urllib.parse.quote(session_id)}"
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            payload_any: Any = json.loads(raw)
            if isinstance(payload_any, dict):
                return cast(Dict[str, Any], payload_any)
            return None
    except Exception:
        return None


def _candidate_local_base_urls(base_url: str) -> List[str]:
    parsed = urllib.parse.urlparse(base_url.strip())
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port

    candidates: List[str] = []

    def _append_candidate(port_value: Optional[int]) -> None:
        netloc = f"{host}:{port_value}" if port_value else host
        url = f"{scheme}://{netloc}"
        if url not in candidates:
            candidates.append(url)

    _append_candidate(port)

    if host in {"127.0.0.1", "localhost"}:
        for fallback_port in COMMON_LOCAL_BASE_PORTS:
            _append_candidate(fallback_port)

    return candidates


def _resolve_reachable_base_url(base_url: str, session_id: str, timeout: float = 1.0) -> str:
    session_q = urllib.parse.quote(session_id)
    paths = (
        f"/v1/mobile/window-tracker/sessions/{session_q}",
        f"/v1/mobile/observer/sessions/{session_q}/signals/latest",
    )

    for candidate in _candidate_local_base_urls(base_url):
        for path in paths:
            url = f"{candidate.rstrip('/')}{path}"
            req = urllib.request.Request(url=url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout):
                    return candidate
            except urllib.error.HTTPError as exc:
                if getattr(exc, "code", None) in {400, 401, 403, 404, 405}:
                    return candidate
            except Exception:
                continue

    return base_url


class FloatingStatusBox:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._enabled = has_tkinter and tk is not None and ttk is not None
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._signal_payload: Optional[Dict[str, Any]] = None
        self._tracker_payload: Optional[Dict[str, Any]] = None
        self._thread: Optional[threading.Thread] = None
        self._root: Any = None
        self._signal_var: Any = None
        self._tracker_var: Any = None
        self._age_var: Any = None
        self._updated_var: Any = None
        self._raw_expiry_var: Any = None
        self._raw_side_var: Any = None
        self._cooldown_var: Any = None
        self._cooldown_remaining_seconds = 0

    def start(self) -> None:
        if not self._enabled:
            LOGGER.warning("Status box unavailable because tkinter could not be loaded.")
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="808-shooter-status-box", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        root = self._root
        if root is not None:
            try:
                root.after(0, root.destroy)
            except Exception:
                pass

    def update(self, signal_payload: Optional[Dict[str, Any]], tracker_payload: Optional[Dict[str, Any]], cooldown_remaining_seconds: int = 0) -> None:
        with self._state_lock:
            self._signal_payload = dict(signal_payload) if isinstance(signal_payload, dict) else None
            self._tracker_payload = dict(tracker_payload) if isinstance(tracker_payload, dict) else None
            self._cooldown_remaining_seconds = max(0, int(cooldown_remaining_seconds))

    def _build_signal_text(self, signal_payload: Optional[Dict[str, Any]], tracker_payload: Optional[Dict[str, Any]]) -> str:
        resolved_payload: Dict[str, Any] = {}
        if isinstance(signal_payload, dict):
            resolved_payload.update(signal_payload)
        if isinstance(tracker_payload, dict):
            for key, value in tracker_payload.items():
                if key not in resolved_payload or resolved_payload.get(key) in (None, "", [], {}):
                    resolved_payload[key] = value

        if not resolved_payload:
            return "Signal: waiting for current Phoenix data"

        status = _truncate_text((tracker_payload or {}).get("status") or resolved_payload.get("status", ""), 16) or "unknown"
        action, action_source = _resolve_display_side(resolved_payload)
        thesis_text = _resolve_thesis_text(resolved_payload)
        next_study = _resolve_next_study_seconds(resolved_payload)
        signal_id = _truncate_text(resolved_payload.get("signal_id", ""), 18) or "n/a"
        action_source_text = _truncate_text(action_source, 18) or "n/a"
        next_text = _format_seconds(next_study)
        return f"Signal: {status} | {action} | {thesis_text} | next {next_text} | {action_source_text} | {signal_id}"

    def _build_tracker_text(self, tracker_payload: Optional[Dict[str, Any]]) -> str:
        if not tracker_payload:
            return "Tracker: waiting for session snapshot"

        status = _truncate_text(tracker_payload.get("status", ""), 16) or "unknown"
        count = tracker_payload.get("capture_count", "n/a")
        frame = tracker_payload.get("frame_index", "n/a")
        next_capture = _resolve_next_study_seconds(tracker_payload)
        focus = _truncate_text(tracker_payload.get("focus_timeframe", ""), 8) or "n/a"
        next_text = _format_seconds(next_capture)
        return f"Tracker: {status} | cap {count} | frame {frame} | next {next_text} | {focus}"

    def _build_raw_expiry_text(self, signal_payload: Optional[Dict[str, Any]]) -> str:
        if not signal_payload:
            return "Expiry raw: n/a"

        resolved_field = signal_payload.get("_resolved_expiry_raw_field")
        resolved_value = signal_payload.get("_resolved_expiry_raw_value")
        if _is_missing_value(resolved_field) or _is_missing_value(resolved_value):
            _resolved_seconds, fallback_field, fallback_value = _resolve_expiry_raw(signal_payload)
            if _is_missing_value(resolved_field):
                resolved_field = fallback_field
            if _is_missing_value(resolved_value):
                resolved_value = fallback_value

        raw_field = _truncate_text(resolved_field or signal_payload.get("expiry_source") or "n/a", 24) or "n/a"
        raw_value = signal_payload.get("_resolved_expiry_raw_value")
        if _is_missing_value(raw_value):
            raw_value = resolved_value
        if _is_missing_value(raw_value):
            raw_value = signal_payload.get("expiry_seconds", "n/a")
        raw_value_text = _truncate_text(raw_value, 18) or "n/a"
        return f"Expiry raw: {raw_field} = {raw_value_text}"

    def _build_raw_side_text(self, signal_payload: Optional[Dict[str, Any]]) -> str:
        if not signal_payload:
            return "Side raw: n/a"

        resolved_side, resolved_source = _resolve_display_side(signal_payload)
        raw_field_value = signal_payload.get("_resolved_side_raw_field") or signal_payload.get("side_source")
        raw_value = signal_payload.get("_resolved_side_raw_value")
        if _is_missing_value(raw_field_value) or _normalize_trade_side(raw_value) not in {"BUY", "SELL"}:
            direct_value = None
            for direct_key in ("execution_action", "action", "side", "candidate_action"):
                candidate = signal_payload.get(direct_key)
                if _normalize_trade_side(candidate) in {"BUY", "SELL"}:
                    raw_field_value = direct_key
                    direct_value = candidate
                    break
            if _normalize_trade_side(direct_value) in {"BUY", "SELL"}:
                raw_value = direct_value
            elif resolved_side in {"BUY", "SELL"}:
                raw_field_value = resolved_source
                raw_value = resolved_side

        raw_field = _truncate_text(raw_field_value or "n/a", 24) or "n/a"
        if _is_missing_value(raw_value):
            raw_value = signal_payload.get("execution_action") or signal_payload.get("action") or signal_payload.get("side") or "n/a"
        raw_value_text = _truncate_text(raw_value, 18) or "n/a"
        return f"Side raw: {raw_field} = {raw_value_text}"

    def _build_cooldown_text(self, cooldown_remaining_seconds: int) -> str:
        if cooldown_remaining_seconds <= 0:
            return "Cooldown: ready"
        minutes, seconds = divmod(int(cooldown_remaining_seconds), 60)
        return f"Cooldown: {minutes}m {seconds:02d}s remaining"

    def _run(self) -> None:
        root = tk.Tk()  # type: ignore[call-arg]
        self._root = root
        root.title("Phoenix Guard Signal Monitor")
        # Start with a reasonable default; we'll auto-resize below to fit content
        root.geometry("420x122+20+20")
        root.resizable(False, False)
        root_any = cast(Any, root)
        root_any.attributes("-topmost", True)  # type: ignore[reportUnknownMemberType]
        try:
            root_any.attributes("-toolwindow", True)  # type: ignore[reportUnknownMemberType]
        except Exception:
            pass
        try:
            root.overrideredirect(True)
        except Exception:
            pass
        root.configure(bg="#111827")
        root.bind("<Unmap>", lambda _event: root.after_idle(root.deiconify))
        root.protocol("WM_DELETE_WINDOW", lambda: None)

        header = tk.Label(  # type: ignore[attr-defined]
            root,
            text=f"Phoenix Guard | {self._session_id}",
            bg="#111827",
            fg="#F9FAFB",
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        )
        header.pack(fill="x", padx=10, pady=(8, 2))

        self._signal_var = tk.StringVar(value="Signal: waiting for current Phoenix data")  # type: ignore[attr-defined]
        self._tracker_var = tk.StringVar(value="Tracker: waiting for session snapshot")  # type: ignore[attr-defined]
        self._age_var = tk.StringVar(value="Latency: n/a")  # type: ignore[attr-defined]
        self._updated_var = tk.StringVar(value="Updated: n/a")  # type: ignore[attr-defined]
        self._raw_expiry_var = tk.StringVar(value="Expiry raw: n/a")  # type: ignore[attr-defined]
        self._raw_side_var = tk.StringVar(value="Side raw: n/a")  # type: ignore[attr-defined]
        self._cooldown_var = tk.StringVar(value="Cooldown: ready")  # type: ignore[attr-defined]

        body_style = {"bg": "+" if False else "#111827", "fg": "#D1D5DB", "anchor": "w", "justify": "left"}
        signal_label = tk.Label(root, textvariable=self._signal_var, **body_style, font=("Segoe UI", 8))  # type: ignore[attr-defined]
        tracker_label = tk.Label(root, textvariable=self._tracker_var, **body_style, font=("Segoe UI", 8))  # type: ignore[attr-defined]
        raw_side_label = tk.Label(root, textvariable=self._raw_side_var, **body_style, font=("Segoe UI", 8))  # type: ignore[attr-defined]
        raw_expiry_label = tk.Label(root, textvariable=self._raw_expiry_var, **body_style, font=("Segoe UI", 8))  # type: ignore[attr-defined]
        age_label = tk.Label(root, textvariable=self._age_var, **body_style, font=("Segoe UI", 8))  # type: ignore[attr-defined]
        cooldown_label = tk.Label(root, textvariable=self._cooldown_var, **body_style, font=("Segoe UI", 8, "bold"))  # type: ignore[attr-defined]
        updated_label = tk.Label(root, textvariable=self._updated_var, **body_style, font=("Segoe UI", 8))  # type: ignore[attr-defined]

        signal_label.pack(fill="x", padx=10)
        tracker_label.pack(fill="x", padx=10, pady=(1, 0))
        raw_side_label.pack(fill="x", padx=10, pady=(1, 0))
        raw_expiry_label.pack(fill="x", padx=10, pady=(1, 0))
        age_label.pack(fill="x", padx=10, pady=(1, 0))
        cooldown_label.pack(fill="x", padx=10, pady=(1, 0))
        updated_label.pack(fill="x", padx=10, pady=(1, 8))

        # Make window draggable even when overrideredirect(True) is set.
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        def _on_press(evt: Any) -> None:
            try:
                # Record the mouse position at press time (screen coords)
                self._drag_offset_x = int(evt.x_root)
                self._drag_offset_y = int(evt.y_root)
            except Exception:
                self._drag_offset_x = 0
                self._drag_offset_y = 0

        def _on_motion(evt: Any) -> None:
            try:
                dx = int(evt.x_root) - int(self._drag_offset_x)
                dy = int(evt.y_root) - int(self._drag_offset_y)
                x = root.winfo_x() + dx
                y = root.winfo_y() + dy
                root.geometry(f"+{x}+{y}")
                # Update stored reference point for smooth dragging
                self._drag_offset_x = int(evt.x_root)
                self._drag_offset_y = int(evt.y_root)
            except Exception:
                return

        # Bind drag events to header and the body labels so user can grab anywhere.
        for widget in (header, signal_label, tracker_label, raw_side_label, raw_expiry_label, age_label, cooldown_label, updated_label):
            try:
                widget.bind("<ButtonPress-1>", _on_press)
                widget.bind("<B1-Motion>", _on_motion)
            except Exception:
                pass

        def _refresh() -> None:
            if self._stop_event.is_set():
                try:
                    root.destroy()
                except Exception:
                    pass
                return

            with self._state_lock:
                signal_payload = dict(self._signal_payload) if self._signal_payload is not None else None
                tracker_payload = dict(self._tracker_payload) if self._tracker_payload is not None else None

            self._signal_var.set(self._build_signal_text(signal_payload, tracker_payload))
            self._tracker_var.set(self._build_tracker_text(tracker_payload))
            self._raw_side_var.set(self._build_raw_side_text(signal_payload))
            self._raw_expiry_var.set(self._build_raw_expiry_text(signal_payload))
            self._cooldown_var.set(self._build_cooldown_text(self._cooldown_remaining_seconds))

            age_sec = None
            if signal_payload is not None:
                age_sec = _display_latency_seconds(signal_payload)
            if age_sec is None and tracker_payload is not None:
                age_sec = _display_latency_seconds(tracker_payload)
            self._age_var.set(f"Latency: {age_sec:.1f}s" if isinstance(age_sec, (int, float)) else "Latency: sync")
            self._updated_var.set(f"Updated: {datetime.now().strftime('%H:%M:%S')}")
            # Auto-resize window to fit contents (avoid truncated text).
            try:
                root.update_idletasks()
                req_widths = [
                    header.winfo_reqwidth(),
                    signal_label.winfo_reqwidth(),
                    tracker_label.winfo_reqwidth(),
                    raw_side_label.winfo_reqwidth(),
                    raw_expiry_label.winfo_reqwidth(),
                    age_label.winfo_reqwidth(),
                    cooldown_label.winfo_reqwidth(),
                    updated_label.winfo_reqwidth(),
                ]
                req_heights = [
                    header.winfo_reqheight(),
                    signal_label.winfo_reqheight(),
                    tracker_label.winfo_reqheight(),
                    raw_side_label.winfo_reqheight(),
                    raw_expiry_label.winfo_reqheight(),
                    age_label.winfo_reqheight(),
                    cooldown_label.winfo_reqheight(),
                    updated_label.winfo_reqheight(),
                ]
                pad_x = 24
                pad_y = 20
                width = max(req_widths) + pad_x
                height = sum(req_heights) + pad_y
                # Only update geometry if it would change noticeably to avoid flicker.
                try:
                    cur_w = root.winfo_width()
                    cur_h = root.winfo_height()
                except Exception:
                    cur_w = 0
                    cur_h = 0
                if abs(cur_w - width) > 6 or abs(cur_h - height) > 6:
                    # Preserve current position if possible.
                    try:
                        x = root.winfo_x()
                        y = root.winfo_y()
                        root.geometry(f"{width}x{height}+{x}+{y}")
                    except Exception:
                        root.geometry(f"{width}x{height}+20+20")
            except Exception:
                pass

            root.after(250, _refresh)

        root.after(0, _refresh)
        try:
            root.mainloop()
        finally:
            self._stop_event.set()


def _normalize_trade_side(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text in _BUY_SIDE_ALIASES or text.startswith("BUY") or text.startswith("BULL"):
        return "BUY"
    if text in _SELL_SIDE_ALIASES or text.startswith("SELL") or text.startswith("BEAR"):
        return "SELL"
    return None


def _coerce_signal_bool(raw: Any) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(raw)


def _entry_state_is_executable(entry_state: Any) -> bool:
    """Check if entry_state indicates execution is ready."""
    text = str(entry_state or "").strip().upper()
    executable_states = {
        "SNIPER_READY",
        "TRIGGER_READY",
        "TRIGGERED",
        "EXECUTE",
        "READY",
        "ACTIVE",
    }
    return text in executable_states


def _resolve_authoritative_execution_side(
    payload: Dict[str, Any],
) -> Tuple[Optional[str], str, str, Any, str]:
    """Return the explicit executable side only; bias/candidate context is advisory."""
    candidates: list[tuple[str, str, Any]] = []
    broker_state = payload.get("broker_execution_state")
    if isinstance(broker_state, dict):
        broker_dict = cast(Dict[str, Any], broker_state)
        broker_side = _normalize_trade_side(broker_dict.get("side"))
        if _coerce_signal_bool(broker_dict.get("actionable", False)) and broker_side in {"BUY", "SELL"}:
            candidates.append((broker_side, "broker_execution_state.side", broker_dict.get("side")))

    if _coerce_signal_bool(payload.get("actionable", False)):
        for field_name in ("execution_action", "side", "action"):
            normalized = _normalize_trade_side(payload.get(field_name))
            if normalized in {"BUY", "SELL"}:
                candidates.append((normalized, field_name, payload.get(field_name)))

    entry_executable = _entry_state_is_executable(payload.get("entry_state"))
    
    if not candidates or (entry_executable and not _normalize_trade_side(payload.get("execution_action")) in {"BUY", "SELL"}):
        if entry_executable:
            for field_name in ("action", "signal_armed_action", "candidate_action", "model_action", "major_bias", "bias_direction", "side"):
                normalized = _normalize_trade_side(payload.get(field_name))
                if normalized in {"BUY", "SELL"}:
                    candidates = [(normalized, f"{field_name}@executable_entry_state", payload.get(field_name))]
                    break

    if not candidates:
        return None, "none", "n/a", None, "payload is not explicitly actionable"

    sides = {side for side, _field, _raw in candidates}
    if len(sides) > 1:
        detail = ", ".join(f"{field}={raw}" for side, field, raw in candidates)
        return None, "conflict", "n/a", None, f"conflicting executable sides: {detail}"

    side, field_name, raw_value = candidates[0]
    return side, f"authoritative_field({field_name})", field_name, raw_value, "explicit"


def _coerce_positive_seconds(raw: Any) -> Optional[int]:
    if isinstance(raw, (int, float)):
        if float(raw) <= 0.0:
            return None
        return int(max(1, round(float(raw))))
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if ":" in text:
            parts = [part.strip() for part in text.split(":") if part.strip()]
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                return max(1, int(parts[0]) * 60 + int(parts[1]))
            if len(parts) == 3 and all(part.isdigit() for part in parts):
                return max(1, int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
        try:
            return int(max(1, round(float(text))))
        except ValueError:
            match = re.search(r"\d+(?:\.\d+)?", text)
            if match:
                return int(max(1, round(float(match.group(0)))))
    return None


def _extract_countdown_seconds(payload: Dict[str, Any]) -> Optional[int]:
    """Extract the current Phoenix countdown from the most explicit fields first."""
    for key in (
        "countdown_seconds",
        "next_event_countdown",
        "countdown_to_inference",
        "next_capture_in_sec",
    ):
        countdown = _coerce_positive_seconds(payload.get(key))
        if countdown:
            return countdown
    return None


def _display_latency_seconds(payload: Dict[str, Any]) -> Optional[float]:
    """Return a conservative display-only latency value.

    This intentionally avoids deriving age from epoch-style tracker fields,
    which can make a fresh session look massively stale even when the live
    observer feed is current.
    """
    for key in (
        "_fetch_latency_sec",
        "fetch_latency_sec",
        "age_sec",
        "age_seconds",
        "latency_sec",
        "latency_seconds",
        "signal_age_sec",
        "signal_age_seconds",
    ):
        raw = payload.get(key)
        if isinstance(raw, (int, float)):
            return max(0.0, float(raw))
        if isinstance(raw, str):
            try:
                return max(0.0, float(raw.strip()))
            except Exception:
                continue
    return None


class RECT(Structure):
    _fields_ = [("left", c_int), ("top", c_int), ("right", c_int), ("bottom", c_int)]


USER32 = ctypes.windll.user32


def set_dpi_awareness() -> None:
    """Set DPI awareness with Windows-compatible fallbacks."""
    try:
        shcore = ctypes.windll.shcore
        if hasattr(shcore, "SetProcessDpiAwareness"):
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            shcore.SetProcessDpiAwareness(2)
            LOGGER.info("DPI awareness enabled via shcore.SetProcessDpiAwareness(2)")
            return
    except Exception:
        pass
    try:
        if hasattr(USER32, "SetProcessDPIAware"):
            USER32.SetProcessDPIAware()
            LOGGER.info("DPI awareness enabled via user32.SetProcessDPIAware()")
            return
    except Exception:
        pass
    LOGGER.info("DPI awareness API not available; continuing without explicit DPI mode")


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    USER32.GetWindowTextW(hwnd, buf, 512)
    return str(buf.value or "")


def _window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buf, 256)
    return str(buf.value or "")


def list_visible_windows(query: Optional[str] = None) -> list[tuple[int, str, str]]:
    """Return visible top-level windows as (hwnd, title, class_name)."""
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


def _is_browser_window(title: str, class_name: str) -> bool:
    title_l = title.lower()
    class_l = class_name.lower()
    return bool(
        "chrome_widgetwin" in class_l
        or "mozillawindowclass" in class_l
        or "applicationframewindow" in class_l
        or "msedge" in class_l
        or "edgehtml" in class_l
        or "edge" in title_l
        or "chrome" in title_l
    )


def _title_has_broker_hint(title: str) -> bool:
    title_l = title.lower()
    return any(
        token in title_l
        for token in (
            "pocket option",
            "pocketoption",
            "trading platform",
            "broker",
            "otc",
            "usd",
            "most innovative",
        )
    )


def find_pocket_option_window(
    window_query: Optional[str] = None,
    *,
    allow_active_fallback: bool = True,
    quiet: bool = False,
) -> Optional[int]:
    """Find target broker window by explicit query, then Pocket Option/trading heuristics."""
    all_windows = list_visible_windows()
    query_miss = False

    if window_query:
        filtered = list_visible_windows(window_query)
        if filtered:
            filtered.sort(key=lambda row: len(row[1]), reverse=True)
            hwnd, title, class_name = filtered[0]
            if not quiet:
                LOGGER.info("Window selected by query '%s': HWND=%s | class=%s | title=%s", window_query, hwnd, class_name, title)
            return hwnd
        query_miss = True

    matches: list[tuple[int, str, str]] = []
    for hwnd, title, class_name in all_windows:
        if "pocket option" in title.lower() or "pocketoption" in title.lower():
            matches.append((hwnd, title, class_name))

    if not matches:
        # Heuristic fallback for broker tabs that hide Pocket Option in title.
        # PRIORITIZE Edge for trading platforms; Chrome is secondary fallback.
        edge_candidates: list[tuple[int, str, str]] = []
        chrome_candidates: list[tuple[int, str, str]] = []
        
        for hwnd, title, class_name in all_windows:
            class_l = class_name.lower()
            
            # Check browser class
            if not _is_browser_window(title, class_name):
                continue
            
            # Check for trading keywords
            if not _title_has_broker_hint(title):
                continue
            
            # Separate Edge from Chrome/other
            if "edge" in class_l or "edge" in title.lower():
                edge_candidates.append((hwnd, title, class_name))
            else:
                chrome_candidates.append((hwnd, title, class_name))
        
        # Try Edge first, then Chrome
        browser_like = edge_candidates if edge_candidates else chrome_candidates

        if browser_like:
            browser_like.sort(key=lambda row: len(row[1]), reverse=True)
            hwnd, title, class_name = browser_like[0]
            browser_type = "Edge" if edge_candidates else "Chrome/Firefox"
            if not quiet:
                if query_miss:
                    LOGGER.info(
                        "No exact visible window matched --window-query '%s'; broker heuristic selected %s window.",
                        window_query,
                        browser_type,
                    )
                LOGGER.warning(
                    "Using heuristic %s fallback: HWND=%s | class=%s | title=%s",
                    browser_type,
                    hwnd,
                    class_name,
                    title,
                )
            return hwnd

        foreground = USER32.GetForegroundWindow() if allow_active_fallback else 0
        if foreground:
            fg_title = _window_title(foreground).strip()
            fg_class = _window_class(foreground).strip()
            if fg_title and _is_browser_window(fg_title, fg_class):
                if not quiet:
                    LOGGER.warning(
                        "Using active window fallback: HWND=%s | class=%s | title=%s",
                        foreground,
                        fg_class,
                        fg_title,
                    )
                return int(foreground)
        if not quiet:
            if query_miss:
                LOGGER.warning("No visible window matched --window-query '%s'", window_query)
            LOGGER.error(
                "Pocket Option window not found. Use 'list-windows' and pass --window-query with part of your broker window title."
            )
        return None

    # Prefer the longest title as it usually includes pair/timeframe tab details.
    matches.sort(key=lambda item: len(item[1]), reverse=True)
    hwnd, title, class_name = matches[0]
    if not quiet:
        LOGGER.info("Found Pocket Option window: HWND=%s | class=%s | title=%s", hwnd, class_name, title)
    return hwnd


def _browser_executable_candidates() -> list[Path]:
    env = {
        "ProgramFiles": Path(os.environ.get("ProgramFiles", str(Path.home()))),
        "ProgramFiles(x86)": Path(os.environ.get("ProgramFiles(x86)", str(Path.home()))),
        "LOCALAPPDATA": Path(os.environ.get("LOCALAPPDATA", str(Path.home()))),
    }

    return [
        env["ProgramFiles(x86)"] / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        env["ProgramFiles"] / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        env["LOCALAPPDATA"] / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        env["ProgramFiles"] / "Google" / "Chrome" / "Application" / "chrome.exe",
        env["ProgramFiles(x86)"] / "Google" / "Chrome" / "Application" / "chrome.exe",
        env["LOCALAPPDATA"] / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]


def open_broker_window(broker_url: str) -> bool:
    """Open Pocket Option in a dedicated browser window."""
    url = str(broker_url or "").strip()
    if not url:
        LOGGER.warning("Broker auto-open skipped because broker URL is empty.")
        return False

    try:
        # Prefer using the OS open semantics (Windows: os.startfile) to avoid
        # forcing new-window flags or changing the user's browser focus/state.
        try:
            if os.name == "nt":
                os.startfile(url)
                LOGGER.info("Opening Pocket Option broker URL via os.startfile: %s", url)
                return True
        except Exception:
            # Fall back to python webbrowser module with 'new=0' to prefer existing window/tab.
            try:
                opened = webbrowser.open(url, new=0)
                if opened:
                    LOGGER.info("Opening Pocket Option broker URL via webbrowser.open: %s", url)
                    return True
            except Exception:
                pass

        browser_path = next((path for path in _browser_executable_candidates() if path.exists()), None)
        if browser_path is not None:
            LOGGER.info("Opening Pocket Option broker window via executable: %s", url)
            subprocess.Popen(
                [str(browser_path), url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True

        LOGGER.warning("Unable to programmatically open browser for URL: %s", url)
        return False
    except Exception as exc:
        LOGGER.error("Failed to open broker window: %s", exc)
        return False


def prepare_pocket_option_window(
    window_query: Optional[str],
    *,
    auto_open: bool = False,
    broker_url: str = DEFAULT_BROKER_URL,
    open_timeout: float = DEFAULT_BROKER_OPEN_TIMEOUT,
    allow_active_fallback: bool = True,
) -> Optional[int]:
    """Find the broker window, optionally opening Pocket Option before failing."""
    hwnd = find_pocket_option_window(window_query, allow_active_fallback=allow_active_fallback, quiet=auto_open)
    if hwnd is not None:
        return hwnd

    if not auto_open:
        return None

    if not open_broker_window(broker_url):
        return None

    deadline = time.time() + max(1.0, float(open_timeout))
    while time.time() < deadline:
        hwnd = find_pocket_option_window(window_query, allow_active_fallback=False, quiet=True)
        if hwnd is not None:
            LOGGER.info("Pocket Option broker window ready after auto-open.")
            return hwnd
        time.sleep(0.50)

    LOGGER.error("Pocket Option did not become visible within %.1fs after auto-open.", float(open_timeout))
    return None


def activate_window(hwnd: int) -> bool:
    """Bring the target window to front without changing its size/state."""
    try:
        try:
            # Only restore minimized windows. Calling SW_RESTORE on an already
            # maximized browser can resize it back to a restored window.
            if bool(USER32.IsIconic(hwnd)):
                USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
        except Exception:
            pass
        USER32.SetForegroundWindow(hwnd)
        USER32.SetFocus(hwnd)
        time.sleep(0.18)
        return True
    except Exception as exc:
        LOGGER.error("Failed to activate target window: %s", exc)
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


def rect_bounds(rect: RECT) -> Tuple[int, int, int, int]:
    return rect.left, rect.top, rect.right, rect.bottom


def load_boxes() -> Dict[str, Dict[str, Any]]:
    if BOXES_FILE.exists():
        try:
            parsed_any = json.loads(BOXES_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed_any, dict) and parsed_any:
                LOGGER.info("Loaded box map from %s", str(BOXES_FILE))
                return cast(Dict[str, Dict[str, Any]], parsed_any)
        except Exception as exc:
            LOGGER.warning("Failed to parse %s: %s", str(BOXES_FILE), exc)

    # Defaults for right-side Pocket Option panel as relative coordinates.
    defaults: Dict[str, Dict[str, float]] = {
        "time_box": {"x": 0.90, "y": 0.26},
        "amount_box": {"x": 0.90, "y": 0.34},
        "buy_button": {"x": 0.90, "y": 0.43},
        "sell_button": {"x": 0.90, "y": 0.49},
        "time_30": {"x": 0.82, "y": 0.23},
        "time_60": {"x": 0.86, "y": 0.23},
        "time_120": {"x": 0.90, "y": 0.23},
        "time_300": {"x": 0.94, "y": 0.23},
    }
    return defaults


def save_boxes(boxes: Dict[str, Dict[str, Any]]) -> None:
    BOXES_FILE.write_text(json.dumps(boxes, indent=2), encoding="utf-8")
    LOGGER.info("Saved box map to %s", str(BOXES_FILE))


def clear_phoenix_cache_backup(workspace_root: Path) -> List[Path]:
    """Safely back up and clear common local Phoenix cache files in `data/`.
    Files are moved to a .bak.TIMESTAMP suffix so the operation is reversible.
    Returns list of backed-up file paths.
    """
    backed_up: List[Path] = []
    data_dir = workspace_root / "data"
    if not data_dir.exists() or not data_dir.is_dir():
        LOGGER.info("clear_cache: no data/ directory found at %s; nothing to clear", str(data_dir))
        return backed_up

    candidate_files = [
        "replay_buffer.jsonl",
        "pending_contexts.json",
        "capture_recovery_state.json",
        "manual_inference_jobs.json",
        "personalization_profiles.json",
        "replay_buffer.jsonl",
        "rl_feedback_buffer.jsonl",
        "feedback_feed.jsonl",
    ]

    ts = int(time.time())
    for fname in candidate_files:
        p = data_dir / fname
        if p.exists() and p.is_file():
            bak = p.with_suffix(p.suffix + f".bak.{ts}")
            try:
                p.rename(bak)
                backed_up.append(bak)
                LOGGER.info("clear_cache: backed up %s -> %s", str(p), str(bak))
            except Exception as exc:
                LOGGER.warning("clear_cache: failed to back up %s: %s", str(p), exc)

    if not backed_up:
        LOGGER.info("clear_cache: no known cache files found to back up in %s", str(data_dir))
    else:
        LOGGER.info("clear_cache: completed backing up %d file(s)", len(backed_up))
    return backed_up


def calibrate_boxes(hwnd: int) -> None:
    """Interactive calibration: user hovers each target and presses Enter.
    
    Full sequence for comprehensive expiry control:
    1. broker_screen - click to remove robot behavior
    2. time_button - open time picker
    3-5. hourly controls: minus, plus, typing input
    6-8. minute controls: minus, plus, typing input
    9-10. buy_icon and sell_icon
    11-14. preset buttons: time_30, time_60, time_120, time_300
    15. final_screen - exit calibration
    
    NOTE: Amount stays fixed at $5 and is NOT calibrated.
    """
    rect = get_window_rect(hwnd)
    if rect is None:
        raise RuntimeError("Failed to read window rectangle during calibration")

    points = [
        "broker_screen",
        "time_button",
        "hourly_minus",
        "hourly_plus",
        "hourly_input",
        "minute_minus",
        "minute_plus",
        "minute_input",
        "buy_icon",
        "sell_icon",
        "time_30",
        "time_60",
        "time_120",
        "time_300",
        "final_screen",
    ]

    LOGGER.info("Calibration started. Keep Pocket Option visible and do not move window.")
    LOGGER.info("For each target: hover mouse on center and press Enter in terminal.")

    calibrated: Dict[str, Dict[str, Any]] = {}
    for name in points:
        input(f"Hover mouse at {name} and press Enter...")
        x, y = pyautogui.position()
        width = max(1, rect.right - rect.left)
        height = max(1, rect.bottom - rect.top)
        rel_x = max(0.0, min(1.0, float(x - rect.left) / float(width)))
        rel_y = max(0.0, min(1.0, float(y - rect.top) / float(height)))
        calibrated[name] = {"x": rel_x, "y": rel_y}
        LOGGER.info("Captured %s => rel(%.4f, %.4f) abs(%s, %s)", name, rel_x, rel_y, x, y)

    save_boxes(calibrated)


def click_at(x: int, y: int, pause: float = 0.14) -> None:
    pyautogui.moveTo(x, y, duration=0.12)
    pyautogui.click(x, y)
    time.sleep(pause)


def show_box_preview(hwnd: int, boxes: Dict[str, Dict[str, Any]]) -> None:
    rect = get_window_rect(hwnd)
    if rect is None:
        raise RuntimeError("Unable to read window for preview")

    LOGGER.info("Previewing mapped points:")
    for name, rel in boxes.items():
        if name == "capabilities":
            LOGGER.info("  %s -> metadata: %s", name, rel)
            continue
        try:
            rx = float(rel.get("x", 0.0))
            ry = float(rel.get("y", 0.0))
        except Exception:
            LOGGER.info("  %s -> invalid coord data: %s", name, rel)
            continue
        abs_pt = rel_to_abs(rect, rx, ry)
        LOGGER.info("  %s -> abs%s rel(%.4f, %.4f)", name, abs_pt, rx, ry)


def set_expiry(hwnd: int, boxes: Dict[str, Dict[str, Any]], expiry: int) -> None:
    rect = get_window_rect(hwnd)
    if rect is None:
        raise RuntimeError("Cannot set expiry: missing window rectangle")

    # Legacy exact preset setter - kept for backwards compatibility
    click_at(*rel_to_abs(rect, boxes["time_box"]["x"], boxes["time_box"]["y"]), pause=0.48)
    time.sleep(0.24)

    key = f"time_{expiry}"
    if key in boxes:
        click_at(*rel_to_abs(rect, boxes[key]["x"], boxes[key]["y"]), pause=0.30)
        pyautogui.press("esc")
        time.sleep(0.22)
        return

    # If exact preset not available, raise to let resolver take control
    raise RuntimeError(f"No calibrated preset point for {expiry}")


def is_broker_ready(hwnd: int, expected_rect: Optional[RECT] = None, tol_px: int = 40) -> bool:
    rect = get_window_rect(hwnd)
    if rect is None:
        return False
    if not USER32.IsWindowVisible(hwnd):
        return False
    left_now, top_now, right_now, bottom_now = rect_bounds(rect)
    if expected_rect is None:
        return True
    # Compare size stability
    width_now = right_now - left_now
    height_now = bottom_now - top_now
    left_exp, top_exp, right_exp, bottom_exp = rect_bounds(expected_rect)
    width_exp = right_exp - left_exp
    height_exp = bottom_exp - top_exp
    if abs(width_now - width_exp) > tol_px or abs(height_now - height_exp) > tol_px:
        LOGGER.warning("Broker window size changed (w:%s->%s h:%s->%s)", width_exp, width_now, height_exp, height_now)
        return False
    return True


def validate_calibration(boxes: Dict[str, Dict[str, Any]], rect: RECT) -> bool:
    # Ensure mapped boxes are within rect and not overlapping dangerously
    left, top, right, bottom = rect_bounds(rect)
    w = right - left
    h = bottom - top
    seen: List[Tuple[str, int, int]] = []
    for name, rel in boxes.items():
        if name == "capabilities":
            continue
        x = left + int(w * float(rel.get("x", 0.0)))
        y = top + int(h * float(rel.get("y", 0.0)))
        if x < left or x > right or y < top or y > bottom:
            LOGGER.error("Calibration point %s out of broker bounds: (%s,%s)", name, x, y)
            return False
        for other_name, ox, oy in seen:
            if abs(ox - x) < 8 and abs(oy - y) < 8:
                LOGGER.warning(
                    "Calibration points close: %s and %s (abs(%s,%s) vs abs(%s,%s)); continuing",
                    name,
                    other_name,
                    x,
                    y,
                    ox,
                    oy,
                )
                # tolerate small overlaps; do not abort calibration here
        seen.append((name, x, y))
    return True


def ocr_read_time_region(hwnd: int, boxes: Dict[str, Dict[str, Any]]) -> Optional[int]:
    if not has_ocr or pytesseract is None:
        return None
    rect = get_window_rect(hwnd)
    if rect is None:
        return None
    left, top, right, bottom = rect_bounds(rect)
    # Try to capture a small region around time_box
    rel = boxes.get("time_box")
    if not rel:
        return None
    x, y = rel_to_abs(rect, rel["x"], rel["y"])
    # grab a small box centered at (x,y)
    left = max(left, x - 80)
    top = max(top, y - 18)
    right = min(right, x + 80)
    bottom = min(bottom, y + 18)
    img = pyautogui.screenshot(region=(left, top, right - left, bottom - top))
    try:
        txt_raw = pytesseract.image_to_string(img, config='--psm 7')
        txt = txt_raw if isinstance(txt_raw, str) else str(txt_raw)
        # crude parse: find number sequence
        m = re.search(r"(\d{1,4})", txt)
        if m:
            return int(m.group(1))
    except Exception:
        return None
    return None


def set_amount(hwnd: int, boxes: Dict[str, Dict[str, Any]], amount: int) -> None:
    rect = get_window_rect(hwnd)
    if rect is None:
        raise RuntimeError("Cannot set amount: missing window rectangle")

    click_at(*rel_to_abs(rect, boxes["amount_box"]["x"], boxes["amount_box"]["y"]), pause=0.15)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.08)
    pyautogui.typewrite(str(amount), interval=0.03)
    time.sleep(0.20)
    pyautogui.press("esc")
    time.sleep(0.12)


def click_trade_button(hwnd: int, boxes: Dict[str, Dict[str, Any]], side: str) -> None:
    rect = get_window_rect(hwnd)
    if rect is None:
        raise RuntimeError("Cannot click trade button: missing window rectangle")

    name = "buy_icon" if side.upper() == "BUY" else "sell_icon"
    x, y = rel_to_abs(rect, boxes[name]["x"], boxes[name]["y"])
    click_at(x, y, pause=0.46)


def _signal_with_tracker_context(signal: Dict[str, Any], wrapper: Dict[str, Any]) -> Dict[str, Any]:
    """Copy a signal and attach timer/tracker context exposed by session payloads."""
    merged = dict(signal)
    wrapper_latest = wrapper.get("latest_signal")
    if isinstance(wrapper_latest, dict) and wrapper_latest is not signal:
        latest_dict = cast(Dict[str, Any], wrapper_latest)
        for key, value in latest_dict.items():
            if _is_missing_value(merged.get(key)):
                merged[key] = value
        for key in (
            "action",
            "base_action",
            "candidate_action",
            "model_action",
            "execution_action",
            "side",
            "major_bias",
            "bias_direction",
            "direction",
        ):
            current_side = _normalize_trade_side(merged.get(key))
            latest_side = _normalize_trade_side(latest_dict.get(key))
            if current_side is None and latest_side in {"BUY", "SELL"}:
                merged[key] = latest_dict[key]
    for key in _TRACKER_SIGNAL_CONTEXT_KEYS:
        if key in wrapper and _is_missing_value(merged.get(key)):
            merged[key] = wrapper[key]

    scenario_analysis = wrapper.get("scenario_analysis")
    if isinstance(scenario_analysis, dict):
        scenario_dict = cast(Dict[str, Any], scenario_analysis)
        if "scenario_analysis" not in merged:
            merged["scenario_analysis"] = dict(scenario_dict)
        if "scenario_analysis_status" not in merged and scenario_dict.get("status"):
            merged["scenario_analysis_status"] = scenario_dict.get("status")
        top_scenario = scenario_dict.get("top_scenario")
        if isinstance(top_scenario, dict):
            top_dict = cast(Dict[str, Any], top_scenario)
            if top_dict.get("direction") and ("scenario_top_direction" not in merged or merged.get("scenario_top_direction") in (None, "", [], {})):
                merged["scenario_top_direction"] = top_dict.get("direction")
            if top_dict.get("probability") is not None and ("scenario_top_probability" not in merged or merged.get("scenario_top_probability") in (None, "", [], {})):
                merged["scenario_top_probability"] = top_dict.get("probability")
            if top_dict.get("transition_type") and ("scenario_top_transition_type" not in merged or merged.get("scenario_top_transition_type") in (None, "", [], {})):
                merged["scenario_top_transition_type"] = top_dict.get("transition_type")

    tracking = wrapper.get("tracking_summary")
    if isinstance(tracking, dict):
        # type-check: ensure tracking is treated as a Dict[str, Any]
        tracking = cast(Dict[str, Any], tracking)
        if "decision_kernel" not in merged and isinstance(tracking.get("decision_kernel"), dict):
            merged["decision_kernel"] = dict(cast(Dict[str, Any], tracking["decision_kernel"]))
        if "support_resistance_zones" not in merged and isinstance(tracking.get("support_resistance_zones"), list):
            merged["support_resistance_zones"] = tracking["support_resistance_zones"]
        for source_key, target_key in (
            ("global_direction", "major_bias"),
            ("local_direction", "bias_direction"),
            ("detected_timeframe", "focus_timeframe"),
            ("detected_market", "market"),
        ):
            value = tracking.get(source_key)
            if value and _is_missing_value(merged.get(target_key)):
                merged[target_key] = value

    return merged


def _signal_payload_is_empty(signal: Dict[str, Any]) -> bool:
    status = str(signal.get("status", "") or "").strip().lower()
    if status in {"empty", "missing", "not_found", "not-found"}:
        return True
    has_identity = bool(_payload_identity_key(signal))
    has_bias = _extract_bias_side(signal)[0] in {"BUY", "SELL"}
    action = _normalize_trade_side(
        signal.get("execution_action")
        or signal.get("action")
        or signal.get("candidate_action")
        or signal.get("side")
    )
    if status in {"awaiting_focus", "warming"} and not has_bias and action is None:
        return True
    if not has_identity and not has_bias and action is None and status in {"", "idle", "awaiting_focus", "warming"}:
        return True
    return False


def _signal_payload_is_stale(signal: Dict[str, Any]) -> bool:
    status = str(signal.get("status", "") or "").strip().lower()
    if status == "stale" or bool(signal.get("stale", False)):
        return True
    freshness = signal.get("freshness_score")
    try:
        if isinstance(freshness, (int, float)) and float(freshness) <= 0.0:
            return True
        if isinstance(freshness, str) and freshness.strip() and float(freshness.strip()) <= 0.0:
            return True
    except Exception:
        pass
    return False


def _extract_signal_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the executable signal dict from either direct or wrapped API payloads."""
    latest_signal = payload.get("latest_signal")
    if isinstance(latest_signal, dict):
        return _signal_with_tracker_context(cast(Dict[str, Any], latest_signal), payload)

    signal = payload.get("signal")
    if isinstance(signal, dict):
        return _signal_with_tracker_context(cast(Dict[str, Any], signal), payload)

    signal_keys = (
        "execution_action",
        "side",
        "action",
        "base_action",
        "candidate_action",
        "model_action",
        "major_bias",
        "bias_direction",
        "best_play_action",
        "thesis_action",
        "actionable",
        "signal_id",
        "dominant_side",
        "decision_kernel",
    )
    if any(key in payload for key in signal_keys):
        return payload
    return None


def fetch_latest_signal(base_url: str, session_id: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    def _get_json(path: str) -> Optional[Dict[str, Any]]:
        url = f"{base_url.rstrip('/')}{path}"
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            payload_any = json.loads(raw)
            if isinstance(payload_any, dict):
                return cast(Dict[str, Any], payload_any)
            return None

    session_q = urllib.parse.quote(session_id)
    tracker_path = f"/v1/mobile/window-tracker/sessions/{session_q}"

    try:
        payload = _get_json(tracker_path)
        if payload is None:
            return None
        signal = _extract_signal_payload(payload)
        if signal is None:
            return None
        if _signal_payload_is_empty(signal):
            LOGGER.debug("Signal fetch via tracker returned empty signal; refusing execution.")
            return None
        if _signal_payload_is_stale(signal):
            LOGGER.debug("Signal fetch via tracker returned stale signal; refusing execution.")
            return None
        signal["_authoritative_signal_endpoint"] = AUTHORITATIVE_SIGNAL_ENDPOINT
        return signal
    except urllib.error.HTTPError as exc:
        if getattr(exc, "code", None) != 404:
            LOGGER.warning("Signal fetch failed via tracker endpoint: %s", exc)
    except urllib.error.URLError as exc:
        LOGGER.warning("Signal fetch failed via tracker endpoint: %s", exc)
    except Exception as exc:
        LOGGER.warning("Signal parse failed via tracker endpoint: %s", exc)

    return None


def parse_trade_signal(payload: Dict[str, Any]) -> Optional[Tuple[str, int, str, str, str, Any, str, Any]]:
    """
    Return (side, expiry, signal_id) for actionable BUY/SELL, else None.
    Includes validation of required signal fields.
    """
    try:
        side = "HOLD"
        side_raw_field = "n/a"
        side_raw_value: Any = None
        expiry_raw_field = "n/a"
        expiry_raw_value: Any = None
        resolved_side, side_source, side_raw_field, side_raw_value, side_reason = _resolve_authoritative_execution_side(payload)
        side = resolved_side or "HOLD"
        actionable = side in {"BUY", "SELL"}
        LOGGER.debug(
            "parse_signal: authoritative side=%s source=%s field=%s reason=%s",
            side or "HOLD",
            side_source,
            side_raw_field,
            side_reason,
        )
        signal_id = _payload_identity_key(payload)
        if ENFORCE_STRICT_EXECUTION and not signal_id:
            LOGGER.warning("parse_signal: missing signal identity in strict mode; rejecting signal")
            return None

        def _normalize_expiry_seconds(seconds: int) -> int:
            # Round to nearest 5s to stabilize noisy kernel updates while keeping precision.
            rounded = int(round(float(seconds) / float(DEFAULT_EXPIRY_ROUNDING_SECONDS)) * DEFAULT_EXPIRY_ROUNDING_SECONDS)
            rounded = max(DEFAULT_MIN_EXPIRY, min(DEFAULT_MAX_EXPIRY, rounded))
            return rounded

        expiry = 0
        expiry_source = "unknown"
        for field_name, cand in _execution_expiry_field_candidates(payload):
            parsed = _parse_expiry_seconds_value(cand)
            if parsed is not None:
                expiry = parsed
                expiry_source = f"signal_field({field_name})"
                expiry_raw_field = field_name
                expiry_raw_value = cand
                break

        # Check for candle notation if no direct expiry found
        if expiry <= 0:
            candle_notation = payload.get("expiry_notation") or payload.get("candle_notation") or payload.get("notation")
            timeframe = payload.get("focus_timeframe") or payload.get("timeframe") or "M5"
            if candle_notation and isinstance(candle_notation, str):
                candle_seconds = _candle_notation_to_seconds(candle_notation, timeframe)
                if candle_seconds and candle_seconds > 0:
                    expiry = candle_seconds
                    expiry_source = f"candle_notation({candle_notation})"
                    expiry_raw_field = "candle_notation"
                    expiry_raw_value = candle_notation

        if expiry <= 0:
            LOGGER.warning("parse_signal: missing explicit expiry; rejecting signal")
            return None
        expiry = _normalize_expiry_seconds(expiry)

        if actionable and side in {"BUY", "SELL"}:
            # Validation successful
            LOGGER.debug("parse_signal: validated %s signal (id=%s, side_from=%s, expiry=%ss from %s, actionable=%s)", side, signal_id, side_source, expiry, expiry_source, actionable)
            LOGGER.info("✅ parse_signal: %s trade confirmed - side from %s, expiry %ss from %s", side, side_source, expiry, expiry_source)
            return side, expiry, signal_id, expiry_source, expiry_raw_field, expiry_raw_value, side_raw_field, side_raw_value
        
        LOGGER.debug("parse_signal: signal not actionable (side=%s from %s, actionable=%s)", side, side_source, actionable)
        return None
    except Exception as exc:
        LOGGER.error("parse_signal: unexpected error: %s", exc)
        return None


def _extract_bias_side(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Extract a BUY/SELL bias hint from signal payload fields.
    Returns (side, source) tuple where source indicates which field provided the side.
    """
    bias_fields: list[tuple[str, Any]] = [
        ("scenario_top_direction", payload.get("scenario_top_direction")),
        ("major_bias", payload.get("major_bias")),
        ("bias_direction", payload.get("bias_direction")),
        ("direction", payload.get("direction")),
        ("thesis_direction", payload.get("thesis_direction")),
        ("thesis_action", payload.get("thesis_action")),
        ("phase_bias", payload.get("phase_bias")),
        ("dominant_side", payload.get("dominant_side")),
        ("candidate_action", payload.get("candidate_action")),
        ("signal_armed_action", payload.get("signal_armed_action")),
        ("best_play_action", payload.get("best_play_action")),
        ("model_action", payload.get("model_action")),
    ]

    scenario_analysis = payload.get("scenario_analysis")
    if isinstance(scenario_analysis, dict):
        scenario_dict = cast(Dict[str, Any], scenario_analysis)
        top_scenario = scenario_dict.get("top_scenario")
        if isinstance(top_scenario, dict):
            top_dict = cast(Dict[str, Any], top_scenario)
            bias_fields.insert(0, ("scenario_analysis.top_scenario.direction", top_dict.get("direction")))
            bias_fields.insert(1, ("scenario_analysis.top_scenario.transition_type", top_dict.get("transition_type")))
        scenarios_any = scenario_dict.get("scenarios")
        if isinstance(scenarios_any, list):
            scenario_dicts = cast(List[Dict[str, Any]], scenarios_any)
            if scenario_dicts:
                first_dict = scenario_dicts[0]
                bias_fields.insert(2, ("scenario_analysis.scenarios[0].direction", first_dict.get("direction")))

    decision_kernel = payload.get("decision_kernel")
    if isinstance(decision_kernel, dict):
        kernel_dict = cast(Dict[str, Any], decision_kernel)
        bias_fields.extend(
            [
                ("decision_kernel.dominant_side", kernel_dict.get("dominant_side")),
                ("decision_kernel.next_candle_bias", kernel_dict.get("next_candle_bias")),
                ("decision_kernel.candle_execution_side", kernel_dict.get("candle_execution_side")),
                ("decision_kernel.countertrend_side", kernel_dict.get("countertrend_side")),
            ]
        )

    tracking_summary = payload.get("tracking_summary")
    if isinstance(tracking_summary, dict):
        tracking_dict = cast(Dict[str, Any], tracking_summary)
        bias_fields.extend(
            [
                ("tracking_summary.global_direction", tracking_dict.get("global_direction")),
                ("tracking_summary.local_direction", tracking_dict.get("local_direction")),
                ("tracking_summary.impulse_direction", tracking_dict.get("impulse_direction")),
            ]
        )
        tracking_kernel = tracking_dict.get("decision_kernel")
        if isinstance(tracking_kernel, dict):
            tracking_kernel_dict = cast(Dict[str, Any], tracking_kernel)
            bias_fields.extend(
                [
                    ("tracking_summary.decision_kernel.dominant_side", tracking_kernel_dict.get("dominant_side")),
                    ("tracking_summary.decision_kernel.next_candle_bias", tracking_kernel_dict.get("next_candle_bias")),
                    ("tracking_summary.decision_kernel.candle_execution_side", tracking_kernel_dict.get("candle_execution_side")),
                ]
            )
        projection = tracking_dict.get("projection")
        if isinstance(projection, dict):
            bias_fields.append(("tracking_summary.projection.direction", cast(Dict[str, Any], projection).get("direction")))

    LOGGER.debug(
        "bias_side: candidates - %s",
        " ".join(f"{name}={value}" for name, value in bias_fields[:12]),
    )

    for field_name, candidate in bias_fields:
        parsed = _normalize_trade_side(candidate)
        if parsed in {"BUY", "SELL"}:
            LOGGER.debug("bias_side: extracted %s from field=%s", parsed, field_name)
            return parsed, f"bias_field({field_name})"

    thesis_text = str(payload.get("current_thesis", "") or "").upper()
    if "BUY" in thesis_text and "SELL" not in thesis_text:
        LOGGER.debug("bias_side: extracted BUY from current_thesis")
        return "BUY", "bias_field(current_thesis)"
    if "SELL" in thesis_text and "BUY" not in thesis_text:
        LOGGER.debug("bias_side: extracted SELL from current_thesis")
        return "SELL", "bias_field(current_thesis)"
    LOGGER.debug("bias_side: no bias side extracted")
    return None, None


def _scenario_analysis_ready_side(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    scenario_analysis = payload.get("scenario_analysis")
    if not isinstance(scenario_analysis, dict):
        return None, None

    scenario_dict = cast(Dict[str, Any], scenario_analysis)
    if str(scenario_dict.get("status", "") or "").strip().lower() != "ready":
        return None, None

    top_scenario = scenario_dict.get("top_scenario")
    if isinstance(top_scenario, dict):
        top_dict = cast(Dict[str, Any], top_scenario)
        top_side = _normalize_trade_side(top_dict.get("direction"))
        if top_side in {"BUY", "SELL"}:
            return top_side, "scenario_analysis.top_scenario.direction"

    scenarios_any = scenario_dict.get("scenarios")
    if isinstance(scenarios_any, list):
        scenario_dicts = cast(List[Dict[str, Any]], scenarios_any)
        if scenario_dicts:
            first_dict = scenario_dicts[0]
            first_side = _normalize_trade_side(first_dict.get("direction"))
            if first_side in {"BUY", "SELL"}:
                return first_side, "scenario_analysis.scenarios[0].direction"

    return None, None


def _signal_has_trigger_consensus(payload: Dict[str, Any], side: str) -> bool:
    target = _normalize_trade_side(side)
    if target not in {"BUY", "SELL"}:
        return False

    consensus_fields = (
        payload.get("scenario_top_direction"),
        payload.get("signal_armed_action"),
        payload.get("candidate_action"),
        payload.get("model_action"),
        payload.get("best_play_action"),
        payload.get("phase_bias"),
        payload.get("major_bias"),
        payload.get("bias_direction"),
        payload.get("direction"),
        payload.get("thesis_action"),
        payload.get("dominant_side"),
    )
    support_count = 0
    for value in consensus_fields:
        if _normalize_trade_side(value) == target:
            support_count += 1

    scenario_side, _scenario_source = _scenario_analysis_ready_side(payload)
    if scenario_side == target:
        support_count += 2

    decision_kernel = payload.get("decision_kernel")
    if isinstance(decision_kernel, dict):
        kernel_dict = cast(Dict[str, Any], decision_kernel)
        for value in (
            kernel_dict.get("dominant_side"),
            kernel_dict.get("next_candle_bias"),
            kernel_dict.get("candle_execution_side"),
        ):
            if _normalize_trade_side(value) == target:
                support_count += 1

    tracking_summary = payload.get("tracking_summary")
    if isinstance(tracking_summary, dict):
        tracking_dict = cast(Dict[str, Any], tracking_summary)
        for value in (
            tracking_dict.get("global_direction"),
            tracking_dict.get("local_direction"),
            tracking_dict.get("impulse_direction"),
        ):
            if _normalize_trade_side(value) == target:
                support_count += 1
        tracking_kernel = tracking_dict.get("decision_kernel")
        if isinstance(tracking_kernel, dict):
            tracking_kernel_dict = cast(Dict[str, Any], tracking_kernel)
            for value in (
                tracking_kernel_dict.get("dominant_side"),
                tracking_kernel_dict.get("next_candle_bias"),
                tracking_kernel_dict.get("candle_execution_side"),
            ):
                if _normalize_trade_side(value) == target:
                    support_count += 1

    kernel_ready = _kernel_requests_trigger(payload)
    return support_count >= 2 and kernel_ready


def _payload_age_seconds(payload: Dict[str, Any]) -> Optional[float]:
    """Best-effort extraction of payload age in seconds."""
    try:
        age_raw = payload.get("age_sec")
        if isinstance(age_raw, (int, float)):
            return max(0.0, float(age_raw))
        if isinstance(age_raw, str):
            parsed = float(age_raw.strip())
            return max(0.0, parsed)
    except Exception:
        pass

    epoch_candidates = (
        payload.get("completed_epoch"),
        payload.get("created_epoch"),
        payload.get("captured_epoch"),
        payload.get("timestamp_epoch"),
    )
    for candidate in epoch_candidates:
        try:
            if isinstance(candidate, (int, float)):
                return max(0.0, time.time() - float(candidate))
            if isinstance(candidate, str) and candidate.strip():
                return max(0.0, time.time() - float(candidate.strip()))
        except Exception:
            continue
    return None


def _payload_is_fresh(payload: Dict[str, Any], max_age_seconds: float) -> bool:
    """Return True when payload is considered fresh enough for execution."""
    status = str(payload.get("status", "") or "").strip().lower()
    stale_flag = bool(payload.get("stale", False))
    freshness_score_raw = payload.get("freshness_score")
    consensus_ready = _signal_has_trigger_consensus(
        payload,
        payload.get("candidate_action")
        or payload.get("model_action")
        or payload.get("best_play_action")
        or payload.get("phase_bias")
        or payload.get("signal_armed_action")
        or payload.get("action")
        or payload.get("execution_action")
        or payload.get("side")
        or "",
    )

    if status == "stale" or stale_flag:
        if consensus_ready and not ENFORCE_STRICT_EXECUTION:
            return True
        return False

    if (
        not ENFORCE_STRICT_EXECUTION
        and _signal_has_trigger_consensus(payload, payload.get("candidate_action") or payload.get("model_action") or payload.get("best_play_action") or payload.get("phase_bias") or payload.get("signal_armed_action") or payload.get("action") or payload.get("execution_action") or payload.get("side") or "")
    ):
        return True

    try:
        if isinstance(freshness_score_raw, (int, float)) and float(freshness_score_raw) <= 0.0:
            return False
        if isinstance(freshness_score_raw, str) and freshness_score_raw.strip() and float(freshness_score_raw.strip()) <= 0.0:
            return False
    except Exception:
        pass

    age = _payload_age_seconds(payload)
    if age is None:
        # If age is not exposed, allow execution based on non-stale status/flags.
        return True
    return age <= max(0.0, max_age_seconds)


def _kernel_requests_trigger(payload: Dict[str, Any]) -> bool:
    """Detect whether kernel/planner indicates immediate trigger intent."""
    trigger_values = (
        payload.get("next_event"),
        payload.get("next_most_likely_event"),
        payload.get("transition"),
        payload.get("decision_kernel_state"),
        payload.get("signal_armed_state"),
        payload.get("execution_permission"),
        payload.get("kernel_state"),
        payload.get("trigger_state"),
        payload.get("armed_state"),
        payload.get("next_trigger"),
        payload.get("next_action"),
        payload.get("entry_state"),
        payload.get("setup_state"),
    )
    for value in trigger_values:
        text = str(value or "").strip().upper()
        if not text:
            continue
        if "TRIGGER" in text or "EXECUTE" in text or "READY_TO_FIRE" in text or text in {"ARMED", "READY", "GO", "ACTIVE"}:
            LOGGER.debug("kernel trigger detected: %s", text)
            return True

    kernel_candidates: List[Dict[str, Any]] = []
    decision_kernel = payload.get("decision_kernel")
    if isinstance(decision_kernel, dict):
        kernel_candidates.append(cast(Dict[str, Any], decision_kernel))
    tracking_summary = payload.get("tracking_summary")
    if isinstance(tracking_summary, dict):
        tracking_kernel = cast(Dict[str, Any], tracking_summary).get("decision_kernel")
        if isinstance(tracking_kernel, dict):
            kernel_candidates.append(cast(Dict[str, Any], tracking_kernel))

    for kernel in kernel_candidates:
        state = str(kernel.get("state", "") or "").strip().upper()
        decision = str(kernel.get("decision", "") or "").strip().upper()
        next_event = str(kernel.get("next_most_likely_event", "") or "").strip().lower()
        if state in {"TRIGGERED", "ACTIVE"}:
            LOGGER.debug("nested kernel trigger detected: state=%s", state)
            return True
        if "TRIGGER" in decision and state in {"ARMED", "READY"}:
            LOGGER.debug("nested kernel trigger watch detected: state=%s decision=%s", state, decision)
            return True
        if state == "ARMED" and next_event == "trigger":
            LOGGER.debug("nested kernel trigger detected: state=%s next_event=%s", state, next_event)
            return True
        p_trigger_next_1 = _coerce_nonnegative_seconds(kernel.get("p_trigger_next_1")) or 0.0
        p_trigger_next_3 = _coerce_nonnegative_seconds(kernel.get("p_trigger_next_3")) or 0.0
        if state == "ARMED" and (p_trigger_next_1 >= 0.35 or p_trigger_next_3 >= 0.55):
            LOGGER.debug("nested kernel trigger probability detected: p1=%.2f p3=%.2f", p_trigger_next_1, p_trigger_next_3)
            return True
    return False


def _extract_trigger_side(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Pick a BUY/SELL side when trigger fallback is active.
    Returns (side, source) tuple.
    """
    trigger_fields = {
        "signal_armed_action": payload.get("signal_armed_action"),
        "candidate_action": payload.get("candidate_action"),
        "model_action": payload.get("model_action"),
        "best_play_action": payload.get("best_play_action"),
        "phase_bias": payload.get("phase_bias"),
        "thesis_action": payload.get("thesis_action"),
    }
    LOGGER.debug("trigger_side: candidates - signal_armed_action=%s candidate_action=%s model_action=%s best_play_action=%s phase_bias=%s thesis_action=%s",
                trigger_fields.get("signal_armed_action", "?"),
                trigger_fields.get("candidate_action", "?"),
                trigger_fields.get("model_action", "?"),
                trigger_fields.get("best_play_action", "?"),
                trigger_fields.get("phase_bias", "?"),
                trigger_fields.get("thesis_action", "?"))
    
    for field_name, candidate in trigger_fields.items():
        parsed = _normalize_trade_side(candidate)
        if parsed in {"BUY", "SELL"}:
            LOGGER.debug("trigger_side: extracted %s from field=%s", parsed, field_name)
            return parsed, f"trigger_field({field_name})"
    
    bias_side, bias_source = _extract_bias_side(payload)
    if bias_side:
        LOGGER.debug("trigger_side: fallback to bias - side=%s source=%s", bias_side, bias_source)
        return bias_side, bias_source
    
    return None, None


def _payload_identity_key(payload: Dict[str, Any]) -> str:
    """Return a stable identity key for signal-change detection."""
    candidates = (
        payload.get("signal_id"),
        payload.get("id"),
        payload.get("bundle_id"),
        payload.get("completed_at"),
        payload.get("created_at"),
        payload.get("captured_at"),
        payload.get("timestamp"),
        payload.get("completed_epoch"),
        payload.get("created_epoch"),
        payload.get("captured_epoch"),
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text

    trigger_state_candidates = (
        ("entry_state", payload.get("entry_state")),
        ("setup_state", payload.get("setup_state")),
        ("trigger_state", payload.get("trigger_state")),
        ("signal_armed_state", payload.get("signal_armed_state")),
        ("armed_state", payload.get("armed_state")),
        ("decision_kernel_state", payload.get("decision_kernel_state")),
        ("kernel_state", payload.get("kernel_state")),
        ("execution_permission", payload.get("execution_permission")),
        ("next_trigger", payload.get("next_trigger")),
    )
    trigger_parts: List[str] = []
    for field_name, raw_value in trigger_state_candidates:
        text = str(raw_value or "").strip()
        if text:
            trigger_parts.append(f"{field_name}={text.upper()}")

    if trigger_parts:
        action_hint = _normalize_trade_side(
            payload.get("execution_action")
            or payload.get("action")
            or payload.get("side")
            or payload.get("signal_armed_action")
            or payload.get("candidate_action")
            or payload.get("model_action")
            or payload.get("best_play_action")
            or payload.get("major_bias")
            or payload.get("bias_direction")
        )
        if action_hint:
            trigger_parts.append(f"action={action_hint}")
        timeframe_hint = str(
            payload.get("focus_timeframe")
            or payload.get("timeframe")
            or payload.get("active_timeframe")
            or ""
        ).strip().upper()
        if timeframe_hint:
            trigger_parts.append(f"timeframe={timeframe_hint}")
        return "|".join(trigger_parts)

    return ""


def _parse_timeframe_minutes(timeframe_str: str) -> int:
    """Parse timeframe like 'M5', 'M1', '5m', 'focus_timeframe' to minutes. Defaults to 5m."""
    text = str(timeframe_str or "").strip().upper()
    if not text:
        return 5
    m = re.search(r'(\d+)', text)
    if m:
        return int(m.group(1))
    return 5


def _candle_notation_to_seconds(notation: str, timeframe_str: str) -> Optional[int]:
    """Convert candle notation like '2c', '5c' to seconds using timeframe.
    Example: '5c' on M5 timeframe = 5 candles * 5 minutes = 25 seconds (25 * 60).
    """
    text = str(notation or "").strip().upper()
    if not text or not text.endswith('C'):
        return None
    try:
        candle_count = int(text[:-1])
        minutes = _parse_timeframe_minutes(timeframe_str)
        total_seconds = max(1, candle_count * minutes * 60)
        LOGGER.info("candle notation: converted %s candles on %sm timeframe => %ds", candle_count, minutes, total_seconds)
        return total_seconds
    except Exception as exc:
        LOGGER.warning("candle notation: failed to parse '%s': %s", notation, exc)
        return None


def _choose_adaptive_expiry(payload: Dict[str, Any], requested_expiry: int, args: Optional[argparse.Namespace] = None) -> int:
    """Choose an adaptive expiry in seconds using Phoenix Guard hints.

    Precedence:
      1. explicit expiry fields in payload
      2. candle notation
      3. explicit countdown fields (countdown_seconds / next_capture_in_sec)
      4. kernel trigger probabilities (p_trigger_next_1 / p_trigger_next_3) -> map to 1/3 candles
      5. scenario analysis confidence -> next candle
      6. fallback to requested_expiry

    Applies rounding and clamps to configured limits.
    """
    def clamp_and_round(sec: int) -> int:
        sec = int(max(DEFAULT_MIN_EXPIRY, min(DEFAULT_MAX_EXPIRY, int(sec or 0))))
        # Round to nearest DEFAULT_EXPIRY_ROUNDING_SECONDS
        rounded = int(round(float(sec) / float(DEFAULT_EXPIRY_ROUNDING_SECONDS)) * DEFAULT_EXPIRY_ROUNDING_SECONDS)
        return max(DEFAULT_MIN_EXPIRY, min(DEFAULT_MAX_EXPIRY, rounded))

    # Diagnostics container
    diagnostics: Dict[str, Any] = {
        "requested_expiry": int(requested_expiry or 0),
        "explicit": None,
        "raw_resolved": None,
        "candle_notation": None,
        "timeframe": None,
        "countdown": None,
        "kernel": {"p1": None, "p3": None, "state": None},
        "scenario_prob": None,
    }

    timeframe = payload.get("focus_timeframe") or payload.get("timeframe") or payload.get("active_timeframe") or "M5"
    diagnostics["timeframe"] = timeframe

    if ENFORCE_STRICT_EXECUTION:
        strict_candidate: Optional[int] = None
        strict_source = "strict_requested_expiry"
        for key, val in _execution_expiry_field_candidates(payload):
            parsed = _parse_expiry_seconds_value(val)
            if parsed and parsed > 0:
                strict_candidate = int(parsed)
                strict_source = f"explicit:{key}"
                diagnostics["explicit"] = {"field": key, "value": int(parsed)}
                break
        if strict_candidate is None:
            raw_resolved = payload.get("_resolved_expiry_raw_value")
            parsed = _parse_expiry_seconds_value(raw_resolved)
            if parsed and parsed > 0:
                strict_candidate = int(parsed)
                strict_source = "raw_resolved"
                diagnostics["raw_resolved"] = int(parsed)
        if strict_candidate is None:
            candle_notation = payload.get("expiry_notation") or payload.get("candle_notation") or payload.get("notation")
            diagnostics["candle_notation"] = candle_notation
            if isinstance(candle_notation, str) and candle_notation.strip().upper().endswith("C"):
                candle_seconds = _candle_notation_to_seconds(candle_notation, timeframe)
                if candle_seconds and candle_seconds > 0:
                    strict_candidate = int(candle_seconds)
                    strict_source = f"candle_notation:{candle_notation}"
        if strict_candidate is None and int(requested_expiry or 0) > 0:
            strict_candidate = int(requested_expiry)
        if strict_candidate is None:
            raise ValueError("strict expiry selection requires explicit payload expiry")
        chosen = clamp_and_round(strict_candidate)
        LOGGER.debug(
            "adaptive_expiry: strict chosen=%ds source=%s diag=%s",
            chosen,
            strict_source,
            json.dumps(diagnostics, default=str),
        )
        return chosen

    candidate_seconds: Optional[int] = None
    candidate_source: str = "fallback"

    # 1) explicit expiry candidates
    for key in ("expiry_seconds", "expiry_sec", "required_seconds", "demo_execution_expiry_seconds"):
        val = payload.get(key)
        parsed = _parse_expiry_seconds_value(val)
        if parsed and parsed > 0:
            diagnostics["explicit"] = {"field": key, "value": int(parsed)}
            candidate_seconds = int(parsed)
            candidate_source = f"explicit:{key}"
            break

    # also consider resolved raw value captured earlier
    if candidate_seconds is None:
        raw_resolved = payload.get("_resolved_expiry_raw_value")
        parsed = _parse_expiry_seconds_value(raw_resolved)
        if parsed and parsed > 0:
            diagnostics["raw_resolved"] = int(parsed)
            candidate_seconds = int(parsed)
            candidate_source = "raw_resolved"

    # 2) candle notation
    if candidate_seconds is None:
        candle_notation = payload.get("expiry_notation") or payload.get("candle_notation") or payload.get("notation")
        diagnostics["candle_notation"] = candle_notation
        if isinstance(candle_notation, str) and candle_notation.strip().upper().endswith("C"):
            candle_seconds = _candle_notation_to_seconds(candle_notation, timeframe)
            if candle_seconds and candle_seconds > 0:
                candidate_seconds = int(candle_seconds)
                candidate_source = f"candle_notation:{candle_notation}"

    # 3) countdown fields
    if candidate_seconds is None:
        countdown = _extract_countdown_seconds(payload)
        diagnostics["countdown"] = countdown
        if countdown and countdown > 0:
            candidate_seconds = int(countdown)
            candidate_source = "countdown"

    # Helper to get timeframe in seconds per candle
    tf_minutes = _parse_timeframe_minutes(str(timeframe or "M5"))
    candle_sec = max(1, tf_minutes * 60)

    # Prepare kernel-candles multiplier (CLI-tunable)
    kernel_candles = 20
    try:
        kernel_candles = int(getattr(args, "kernel_candles", kernel_candles))
    except Exception:
        kernel_candles = 20

    # 4) decision kernel probabilities
    if candidate_seconds is None:
        decision_kernel = payload.get("decision_kernel")
        if isinstance(decision_kernel, dict):
            kernel_dict = cast(Dict[str, Any], decision_kernel)
            p1 = _coerce_nonnegative_seconds(kernel_dict.get("p_trigger_next_1")) or 0.0  # type: ignore[reportUnknownMemberType]
            p3 = _coerce_nonnegative_seconds(kernel_dict.get("p_trigger_next_3")) or 0.0  # type: ignore[reportUnknownMemberType]
            state = str(kernel_dict.get("state") or "").strip().upper()  # type: ignore[reportUnknownMemberType]
            diagnostics["kernel"] = {"p1": float(p1), "p3": float(p3), "state": state}
            if state in {"ARMED", "TRIGGERED"}:
                # Immediate kernel state implies near-term trigger (keep 1 candle)
                candidate_seconds = int(candle_sec)
                candidate_source = f"kernel_state:{state}"
            elif p1 >= 0.35:
                # Treat kernel-probability hints as longer-horizon forecasts.
                # Use configurable `kernel_candles` to allow multi-candle setups.
                candidate_seconds = int(candle_sec * max(1, int(kernel_candles)))
                candidate_source = f"kernel_p1_{kernel_candles}c"
            elif p3 >= 0.55:
                # Stronger multi-step probability - also map to configured candles
                candidate_seconds = int(candle_sec * max(1, int(kernel_candles)))
                candidate_source = f"kernel_p3_{kernel_candles}c"

    # 5) scenario analysis confidence
    if candidate_seconds is None:
        scenario_analysis = payload.get("scenario_analysis")
        if isinstance(scenario_analysis, dict):
            scenario_dict = cast(Dict[str, Any], scenario_analysis)
            top = scenario_dict.get("top_scenario")  # type: ignore[reportUnknownMemberType]
            if isinstance(top, dict):
                top_dict = cast(Dict[str, Any], top)
                prob = top_dict.get("probability")
                try:
                    if isinstance(prob, (int, float)) and float(prob) >= 0.7:
                        diagnostics["scenario_prob"] = float(prob)
                        candidate_seconds = int(candle_sec)
                        candidate_source = "scenario_top_confident"
                except Exception:
                    pass

    # 6) fallback to requested_expiry
    if candidate_seconds is None:
        diagnostics["note"] = "falling back to requested"
        candidate_seconds = int(requested_expiry or DEFAULT_EXPIRY_FALLBACK_SECONDS)
        candidate_source = "fallback"

    # Finalize and log diagnostics (sanitized sample)
    chosen = clamp_and_round(int(candidate_seconds or DEFAULT_EXPIRY_FALLBACK_SECONDS))
    try:
        sample_payload = {
            "signal_id": payload.get("signal_id") or payload.get("id") or None,
            "expiry_notation": payload.get("expiry_notation") or payload.get("candle_notation") or payload.get("notation"),
            "focus_timeframe": timeframe,
        }
        # Respect CLI verbosity flag when present
        verbose_flag = False
        try:
            verbose_flag = bool(getattr(args, "adaptive_verbose", False))
        except Exception:
            verbose_flag = False

        if verbose_flag:
            LOGGER.info(
                "adaptive_expiry: chosen=%ds source=%s diag=%s sample=%s",
                chosen,
                candidate_source,
                json.dumps(diagnostics, default=str),
                json.dumps(sample_payload, default=str),
            )
        else:
            LOGGER.debug(
                "adaptive_expiry: chosen=%ds source=%s diag=%s sample=%s",
                chosen,
                candidate_source,
                json.dumps(diagnostics, default=str),
                json.dumps(sample_payload, default=str),
            )
    except Exception:
        LOGGER.debug("adaptive_expiry: chosen=%ds source=%s (diag logging failed)", chosen, candidate_source)

    return chosen


def run_adaptive_test(args: argparse.Namespace) -> int:
    """Run a few synthetic payloads through `_choose_adaptive_expiry` and print results."""
    examples: List[Tuple[Dict[str, Any], int]] = []
    # 1) explicit expiry
    examples.append(({"expiry_seconds": 120, "signal_id": "ex1"}, 120))
    # 2) candle notation on M5 -> 5 candles * 5m = 25min -> seconds
    examples.append(({"expiry_notation": "5c", "focus_timeframe": "M5", "signal_id": "ex2"}, 0))
    # 3) countdown field
    examples.append(({"countdown_seconds": 90, "signal_id": "ex3"}, 0))
    # 4) kernel p1 hint
    examples.append(({"decision_kernel": {"p_trigger_next_1": 0.4, "state": "ARMED"}, "signal_id": "ex4"}, 0))
    # 5) kernel p1 but not armed
    examples.append(({"decision_kernel": {"p_trigger_next_1": 0.4}, "focus_timeframe": "M1", "signal_id": "ex5"}, 0))

    print("Adaptive expiry test (kernel_candles=%s):" % getattr(args, "kernel_candles", 20))
    for payload, requested in examples:
        # types: payload: Dict[str, Any], requested: int
        chosen = _choose_adaptive_expiry(payload, int(requested or 0), args)
        print(json.dumps({"signal_id": payload.get("signal_id"), "requested": requested, "chosen": chosen, "payload": payload}, default=str))
    return 0

def _fetch_phoenix_countdown_and_timeframe(base_url: str, session_id: str, timeout: float = 1.5) -> Tuple[Optional[int], Optional[str]]:
    """Fetch Phoenix's current countdown (seconds to next inference) and active timeframe.
    Returns (countdown_seconds, timeframe_str) or (None, None) on failure.
    """
    def _get_json(path: str) -> Optional[Dict[str, Any]]:
        try:
            url = f"{base_url.rstrip('/')}{path}"
            req = urllib.request.Request(url=url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                payload_any: Any = json.loads(raw)
                if isinstance(payload_any, dict):
                    return cast(Dict[str, Any], payload_any)
                return None
        except Exception:
            return None

    session_q: str = urllib.parse.quote(session_id)

    def _extract_countdown(payload: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
        signal = _extract_signal_payload(payload) or {}
        sources: list[Dict[str, Any]] = [signal, payload]
        for source in sources:
            timeframe_raw = (
                source.get("focus_timeframe")
                or source.get("timeframe")
                or source.get("active_timeframe")
                or "M5"
            )
            countdown_sec = _extract_countdown_seconds(source)
            if countdown_sec:
                return countdown_sec, str(timeframe_raw)

            # Keep interval fields as a last-resort fallback so we still have a
            # visible timer if Phoenix omits explicit countdown data.
            for key in (
                "effective_capture_interval_sec",
                "capture_interval_sec",
            ):
                countdown_sec = _coerce_positive_seconds(source.get(key))
                if countdown_sec:
                    return countdown_sec, str(timeframe_raw)
        return None, None

    for path in (
        f"/v1/mobile/observer/sessions/{session_q}/signals/latest",
        f"/v1/mobile/window-tracker/sessions/{session_q}",
    ):
        try:
            payload = _get_json(path)
            if payload:
                countdown_sec, timeframe = _extract_countdown(payload)
                if countdown_sec:
                    return countdown_sec, timeframe
        except Exception:
            pass

    LOGGER.warning("countdown fetch: unable to retrieve Phoenix countdown or timeframe")
    return None, None


def execute_trade(hwnd: int, boxes: Dict[str, Dict[str, Any]], side: str, expiry: int, amount: int) -> bool:
    """Execute a single trade with comprehensive error handling and validation."""
    try:
        LOGGER.info("\n=== TRADE SHOT ===")
        LOGGER.info("Direction=%s | Expiry=%ss | Amount=$%s", side, expiry, amount)
        LOGGER.info("⏱️  Executing with Phoenix explicit expiry: %s seconds", expiry)
        LOGGER.info("Timestamp=%s", datetime.now().isoformat(timespec="seconds"))

        if not activate_window(hwnd):
            LOGGER.error("execute: activation failed")
            return False

        rect = get_window_rect(hwnd)
        if rect is None:
            LOGGER.error("execute: missing window rect")
            return False

        # HARDENING: Validate calibration integrity
        caps = boxes.get("capabilities", {})
        if not validate_calibration(boxes, rect):
            LOGGER.error("execute: calibration validation failed; aborting trade")
            return False
        LOGGER.debug("execute: calibration validation passed")

        # HARDENING: Check broker readiness
        if not is_broker_ready(hwnd, rect):
            LOGGER.error("execute: broker not ready or window changed; aborting trade")
            return False
        LOGGER.debug("execute: broker readiness check passed")

        # Use adaptive resolver which enforces safeguards and verification.
        if not resolve_and_set_expiry(hwnd, boxes, expiry, caps):
            LOGGER.error("execute: failed to set requested Phoenix expiry %ss; aborting trade", expiry)
            return False

        # Do NOT change amount — amount is fixed by broker settings. Only click trade button.
        click_trade_button(hwnd, boxes, side)
        LOGGER.info("execute: CLICK SENT => %s", side)
        return True
    except Exception as exc:
        LOGGER.error("execute: trade shot failed with exception: %s", exc)
        return False


def run_manual(args: argparse.Namespace) -> int:
    hwnd = find_pocket_option_window(args.window_query)
    if hwnd is None:
        return 2
    if not activate_window(hwnd):
        return 2

    boxes = load_boxes()
    # Enforce calibration integrity before starting automated signal mode.
    rect = get_window_rect(hwnd)
    if rect is None or not validate_calibration(boxes, rect):
        LOGGER.error(
            "Calibration invalid or missing for target window; aborting signal mode. Run 'calibrate' to set accurate boxes."
        )
        return 2
    show_box_preview(hwnd, boxes)

    ok = execute_trade(hwnd, boxes, args.side.upper(), args.expiry, args.amount)
    return 0 if ok else 1


def _toggle_automatic_trigger(source: str = "Ctrl+B") -> None:
    """Toggle automatic execution with a shared debounce across listener backends."""
    global automatic_trigger_enabled, last_hotkey_toggle_ts

    now = time.time()
    with automatic_trigger_lock:
        if now - last_hotkey_toggle_ts < HOTKEY_DEBOUNCE_SECONDS:
            LOGGER.debug("Hotkey debounced: toggle too frequent")
            return

        automatic_trigger_enabled = not automatic_trigger_enabled
        last_hotkey_toggle_ts = now
        state = "ENABLED" if automatic_trigger_enabled else "DISABLED"

    LOGGER.info("AUTOMATIC TRIGGER %s (%s) @ %s", state, source, datetime.now().strftime("%H:%M:%S"))


def _pause_automatic_trigger_for(seconds: float, source: str = "safety checkpoint") -> None:
    """Pause automatic execution temporarily and restore it after the delay."""
    global automatic_trigger_enabled

    duration = max(1.0, float(seconds))
    with automatic_trigger_lock:
        automatic_trigger_enabled = False

    LOGGER.info("AUTOMATIC TRIGGER PAUSED (%s) for %.0fs", source, duration)

    def _resume_later() -> None:
        try:
            global automatic_trigger_enabled
            time.sleep(duration)
            with automatic_trigger_lock:
                automatic_trigger_enabled = True
            LOGGER.info("AUTOMATIC TRIGGER RESUMED (%s) after %.0fs", source, duration)
        except Exception as exc:
            LOGGER.warning("Failed to resume automatic trigger after pause (%s): %s", source, exc)

    threading.Thread(target=_resume_later, name="808-shooter-safety-pause", daemon=True).start()


def _random_safety_pause_seconds() -> int:
    return random.randint(SAFETY_PAUSE_MIN_SECONDS, SAFETY_PAUSE_MAX_SECONDS)


def _start_native_ctrl_b_listener() -> bool:
    """Use Win32 key-state polling when the optional keyboard package is missing."""
    try:
        get_async_key_state = USER32.GetAsyncKeyState
        get_async_key_state.argtypes = [c_int]
        get_async_key_state.restype = c_int
    except Exception as exc:
        LOGGER.warning("Native Ctrl+B listener unavailable: %s", exc)
        return False

    def _poll_ctrl_b() -> None:
        was_pressed = False
        while True:
            try:
                ctrl_down = bool(
                    get_async_key_state(VK_CONTROL) & 0x8000
                    or get_async_key_state(VK_LCONTROL) & 0x8000
                    or get_async_key_state(VK_RCONTROL) & 0x8000
                )
                b_down = bool(get_async_key_state(VK_B) & 0x8000)
                pressed = bool(ctrl_down and b_down)
                if pressed and not was_pressed:
                    _toggle_automatic_trigger("Ctrl+B/native")
                was_pressed = pressed
                time.sleep(0.05)
            except Exception as exc:
                LOGGER.warning("Native Ctrl+B listener stopped: %s", exc)
                return

    thread = threading.Thread(target=_poll_ctrl_b, name="808-shooter-ctrl-b", daemon=True)
    thread.start()
    LOGGER.info("Hotkey listener active via Windows native polling: Press Ctrl+B to toggle automatic trigger")
    return True


def setup_hotkey_listener() -> bool:
    """Listen for Ctrl+B hotkey to toggle automatic trigger mode."""
    if has_keyboard and keyboard is not None:
        try:
            keyboard.add_hotkey("ctrl+b", lambda: _toggle_automatic_trigger("Ctrl+B/keyboard"))
            LOGGER.info(
                "Hotkey listener active via keyboard package: Press Ctrl+B to toggle automatic trigger (debounce %.1fs)",
                HOTKEY_DEBOUNCE_SECONDS,
            )
            return True
        except Exception as exc:
            LOGGER.warning("keyboard package hotkey registration failed; falling back to Windows native listener: %s", exc)
    else:
        LOGGER.info("keyboard package not installed; using Windows native Ctrl+B listener")

    return _start_native_ctrl_b_listener()


def resolve_and_set_expiry(hwnd: int, boxes: Dict[str, Dict[str, Any]], expiry: int, caps: Dict[str, Any]) -> bool:
    """Set expiry using comprehensive calibrated controls.

    Priority order:
      1) Exact preset match (`time_30/60/120/300`)
      2) Hourly + minute breakdown with typing inputs
      3) Hourly + minute with +/- adjustment
      4) Fallback preset when strict mode is off
    """
    try:
        rect = get_window_rect(hwnd)
        if rect is None:
            LOGGER.error("resolver: window rect unavailable")
            return False

        # Step 1: Open time picker
        click_at(*rel_to_abs(rect, boxes["time_button"]["x"], boxes["time_button"]["y"]), pause=0.48)
        time.sleep(0.24)

        # Step 2: Try exact preset
        def _preset_key_for(value: int) -> Optional[str]:
            legacy = f"time_{value}"
            if legacy in boxes:
                return legacy
            modern = f"time_preset_{value}"
            if modern in boxes:
                return modern
            return None

        exact_key = _preset_key_for(expiry)
        if exact_key is not None:
            click_at(*rel_to_abs(rect, boxes[exact_key]["x"], boxes[exact_key]["y"]), pause=0.30)
            pyautogui.press("esc")
            time.sleep(0.22)
            LOGGER.info("resolver: selected exact preset %ss via %s", expiry, exact_key)
            return True

        # Step 3: Hourly + minute breakdown with typing
        if all(key in boxes for key in ["hourly_input", "minute_input"]):
            try:
                hours = expiry // 3600
                minutes = (expiry % 3600) // 60
                
                # Input hours
                click_at(*rel_to_abs(rect, boxes["hourly_input"]["x"], boxes["hourly_input"]["y"]), pause=0.22)
                time.sleep(0.12)
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.08)
                pyautogui.typewrite(str(int(hours)), interval=0.04)
                time.sleep(0.15)
                
                # Input minutes
                click_at(*rel_to_abs(rect, boxes["minute_input"]["x"], boxes["minute_input"]["y"]), pause=0.22)
                time.sleep(0.12)
                pyautogui.hotkey("ctrl", "a")
                time.sleep(0.08)
                pyautogui.typewrite(str(int(minutes)), interval=0.04)
                time.sleep(0.15)
                
                pyautogui.press("enter")
                time.sleep(0.22)
                LOGGER.info("resolver: set via hourly+minute typing: %dh %dm (%ds total)", hours, minutes, expiry)
                return True
            except Exception as exc:
                LOGGER.warning("resolver: hourly+minute typing failed: %s", exc)

        # Step 4: Hourly + minute with +/- adjustment
        if all(key in boxes for key in ["hourly_minus", "hourly_plus", "minute_minus", "minute_plus"]):
            try:
                hours = expiry // 3600
                minutes = (expiry % 3600) // 60
                
                # Adjust hours
                for _ in range(int(hours)):
                    click_at(*rel_to_abs(rect, boxes["hourly_plus"]["x"], boxes["hourly_plus"]["y"]), pause=0.10)
                
                # Adjust minutes
                for _ in range(int(minutes)):
                    click_at(*rel_to_abs(rect, boxes["minute_plus"]["x"], boxes["minute_plus"]["y"]), pause=0.10)
                
                pyautogui.press("esc")
                time.sleep(0.22)
                LOGGER.info("resolver: set via +/- adjustment: %dh %dm (%ds total)", hours, minutes, expiry)
                return True
            except Exception as exc:
                LOGGER.warning("resolver: +/- adjustment failed: %s", exc)

        # Step 5: Fallback preset
        if not ENFORCE_STRICT_EXECUTION:
            fallback_key = _preset_key_for(DEFAULT_EXPIRY_FALLBACK_SECONDS)
            if fallback_key is not None:
                click_at(*rel_to_abs(rect, boxes[fallback_key]["x"], boxes[fallback_key]["y"]), pause=0.30)
                pyautogui.press("esc")
                time.sleep(0.22)
                LOGGER.warning("resolver: strict mode off, using fallback preset %ss", DEFAULT_EXPIRY_FALLBACK_SECONDS)
                return True

        LOGGER.error("resolver: no expiry control path available for %ss", expiry)
        return False
    except Exception as exc:
        LOGGER.error("resolver: unexpected error: %s", exc)
        return False


def fetch_phoenix_major_bias(base_url: str, session_id: str, timeout: float = 3.0) -> Optional[str]:
    """Fetch the major bias (BUY/SELL direction) from Phoenix analysis.
    
    Tries multiple endpoints to find bias information:
    - signals endpoint with major_bias field
    - tracker session with recent analysis
    Returns "BUY", "SELL", or None if not available.
    """
    latest = fetch_latest_signal(base_url, session_id, timeout=timeout)
    if latest:
        bias, bias_source = _extract_bias_side(latest)
        if bias in {"BUY", "SELL"}:
            LOGGER.info("test_signal: Phoenix bias detected: %s from %s", bias, bias_source or "latest_signal")
            return bias

    def _get_json(path: str) -> Optional[Dict[str, Any]]:
        try:
            url = f"{base_url.rstrip('/')}{path}"
            req = urllib.request.Request(url=url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                payload_any: Any = json.loads(raw)
                if isinstance(payload_any, dict):
                    return cast(Dict[str, Any], payload_any)
                return None
        except Exception:
            return None

    session_q: str = urllib.parse.quote(session_id)
    
    # Try observer endpoint for major bias
    try:
        payload: Optional[Dict[str, Any]] = _get_json(f"/v1/mobile/observer/sessions/{session_q}/signals/latest")
        if payload:
            signal = _extract_signal_payload(payload) or payload
            bias_str, bias_source = _extract_bias_side(signal)
            if bias_str in {"BUY", "SELL"}:
                LOGGER.info("test_signal: Phoenix major bias detected: %s from %s", bias_str, bias_source or "observer")
                return bias_str
    except Exception:
        pass
    
    # Try tracker session for analysis summary
    try:
        payload = _get_json(f"/v1/mobile/window-tracker/sessions/{session_q}")
        if payload:
            signal = _extract_signal_payload(payload) or payload
            bias_str, bias_source = _extract_bias_side(signal)
            if bias_str in {"BUY", "SELL"}:
                LOGGER.info("test_signal: Phoenix major bias from tracker: %s from %s", bias_str, bias_source or "tracker")
                return bias_str
    except Exception:
        pass
    
    LOGGER.warning("test_signal: Could not fetch Phoenix major bias; startup test will wait instead of random-firing")
    return None


def generate_test_signal(base_url: str, session_id: str, fallback_expiry: int = TEST_SIGNAL_EXPIRY_SECONDS) -> Dict[str, Any]:
    """Generate a test signal based on Phoenix's major bias.
    
    The signal respects Phoenix's direction preference and is configured
    for immediate execution using Phoenix's current countdown when available.
    """
    bias: str
    bias_opt: Optional[str] = fetch_phoenix_major_bias(base_url, session_id)
    
    # Ensure bias is always a valid string (BUY or SELL)
    if bias_opt in {"BUY", "SELL"}:
        bias = bias_opt
    else:
        test_signal = {
            "signal_id": f"test_waiting_for_bias_{int(time.time())}",
            "status": "TEST_WAITING_FOR_PHOENIX_BIAS",
            "action": "HOLD",
            "execution_action": "HOLD",
            "side": "HOLD",
            "actionable": False,
            "expiry_seconds": max(1, int(fallback_expiry or TEST_SIGNAL_EXPIRY_SECONDS)),
            "test_mode": True,
            "created_at": datetime.now().isoformat(),
            "summary": "Startup test is waiting because Phoenix has not exposed a BUY/SELL bias yet.",
        }
        LOGGER.warning("test_signal: Phoenix bias missing; refusing to random-fire startup test entry")
        return test_signal

    countdown_sec, timeframe = _fetch_phoenix_countdown_and_timeframe(base_url, session_id)
    expiry = int(countdown_sec or max(1, int(fallback_expiry or TEST_SIGNAL_EXPIRY_SECONDS)))
    
    test_signal: Dict[str, Any] = {
        "signal_id": f"test_signal_{int(time.time())}",
        "status": "TEST",
        "action": bias,
        "execution_action": bias,
        "side": bias,
        "actionable": True,
        "expiry_seconds": expiry,
        "focus_timeframe": timeframe or "",
        "test_mode": True,
        "created_at": datetime.now().isoformat(),
    }
    
    LOGGER.info("test_signal: Generated test signal -> %s @ %ss expiry", bias, expiry)
    return test_signal


def run_signal_loop(args: argparse.Namespace) -> int:
    global automatic_trigger_enabled, last_stable_signal
    # Optionally clear local Phoenix/cache files before starting signal mode.
    try:
        if bool(getattr(args, "clear_phoenix_cache", False)):
            ws_root = Path(__file__).parent
            LOGGER.info("clear_cache: backing up and clearing known Phoenix cache files in %s/data", str(ws_root))
            backed = clear_phoenix_cache_backup(ws_root)
            LOGGER.info("clear_cache: backed up %d file(s)", len(backed))
    except Exception as exc:
        LOGGER.warning("clear_cache: unexpected error during cache clear: %s", exc)

    hwnd = prepare_pocket_option_window(
        args.window_query,
        auto_open=bool(getattr(args, "auto_open_broker", False)),
        broker_url=str(getattr(args, "broker_url", DEFAULT_BROKER_URL)),
        allow_active_fallback=False,
    )
    if hwnd is None:
        return 2
    if not activate_window(hwnd):
        return 2

    resolved_base_url = _resolve_reachable_base_url(args.base_url, args.session_id, timeout=1.0)
    if resolved_base_url.rstrip("/") != args.base_url.rstrip("/"):
        LOGGER.warning(
            "Signal API base URL auto-corrected: %s -> %s",
            args.base_url,
            resolved_base_url,
        )
        args.base_url = resolved_base_url
    else:
        LOGGER.info("Signal API base URL: %s", args.base_url)

    boxes = load_boxes()
    show_box_preview(hwnd, boxes)

    hotkey_active = setup_hotkey_listener()
    if not hotkey_active:
        LOGGER.warning("Ctrl+B hotkey listener unavailable; automatic trigger remains %s", automatic_trigger_enabled)

    last_signal_id = ""
    last_non_actionable_status = ""
    last_shot_ts = 0.0
    last_waiting_log_ts = 0.0
    last_auto_arm_wait_log_ts = 0.0
    last_tracker_snapshot: Optional[Dict[str, Any]] = None
    last_tracker_fetch_ts = 0.0
    startup_identity_key = ""
    startup_gate_open = not bool(getattr(args, "strict_new_signal_mode", True))
    startup_expiry_snapped = False
    trades_executed_count = 0  # Safety wait mechanism counter
    SAFETY_WAIT_INTERVAL = 5  # Pause & allow pair change every N trades
    # First-trade denial is disabled; strict signal identity and stability gates
    # handle startup safety without delaying the first confirmed tracker signal.
    # All Phoenix Guard trades execute immediately without initial skip
    # ignore_first_phoenix_trade = True
    ignore_first_phoenix_trade = False
    MAX_TRADES_PER_WINDOW = 5
    WINDOW_DURATION_SECONDS = 300
    trades_in_window = 0
    # If user only wants to run adaptive-expiry tests, do that and exit.
    if bool(getattr(args, "adaptive_test", False)):
        try:
            return run_adaptive_test(args)
        except Exception as exc:
            LOGGER.error("adaptive-test failed: %s", exc)
            return 2
    window_start_ts = 0.0
    cooldown_expiry_ts = 0.0
    last_cooldown_log_ts = 0.0
    
    # Test signal state machine
    # DISABLED: Test signal injection commented out per user request
    # test_signal_active = bool(getattr(args, "test_signal", False))
    # test_signal_start_time = 0.0
    # test_signal_executed = False
    # 
    # if test_signal_active:
    #     LOGGER.info("\n" + "="*70)
    #     LOGGER.info("🧪 TEST SIGNAL MODE ACTIVE")
    #     LOGGER.info("A 30-second test signal will be injected based on Phoenix's major bias")
    #     LOGGER.info("even if the system is in HOLD. This lets you verify the setup works.")
    #     LOGGER.info("="*70 + "\n")
    #     test_signal_start_time = time.time()
    test_signal_active = False
    test_signal_start_time = 0.0
    test_signal_executed = False

    status_box = FloatingStatusBox(args.session_id)
    status_box.start()
    
    LOGGER.info("Signal mode live. Poll=%ss | Cooldown=%ss | Automatic Trigger=%s", args.poll, args.cooldown, automatic_trigger_enabled)
    LOGGER.info("Press Ctrl+B to toggle automatic trade execution")
    LOGGER.info("Safety mechanism: Every %d trades, a pause will allow pair change", SAFETY_WAIT_INTERVAL)

    # AUTO-ARM: Snap to Phoenix countdown on startup
    if bool(getattr(args, "auto_arm_collaboration", True)) and not ENFORCE_STRICT_EXECUTION:
        LOGGER.info("\n" + "="*70)
        LOGGER.info("🤝 AUTO-ARM COLLABORATION: Snapping to Phoenix Guard countdown...")
        LOGGER.info("="*70 + "\n")
        countdown_sec, timeframe_str = _fetch_phoenix_countdown_and_timeframe(args.base_url, args.session_id)
        if countdown_sec and countdown_sec > 0:
            LOGGER.info("AUTO-ARM: Snapped to Phoenix countdown %ds on %s timeframe", countdown_sec, timeframe_str or "unknown")
            args.expiry = int(countdown_sec)
        else:
            LOGGER.warning("AUTO-ARM: Could not fetch countdown; using default expiry %ds", args.expiry)
        # Enable first-trade execution regardless of countdown fetch success
        startup_expiry_snapped = True
    elif bool(getattr(args, "auto_arm_collaboration", True)):
        LOGGER.info("AUTO-ARM disabled by strict execution: waiting for explicit Phoenix tracker signals only")

    try:
        while True:
            try:
            # Handle test signal injection window
                # DISABLED: Test signal injection commented out per user request
                # if test_signal_active and not test_signal_executed:
                #     elapsed = time.time() - test_signal_start_time
                #     if elapsed < TEST_SIGNAL_TIMEOUT_SECONDS:
                #         LOGGER.info("test_signal: Window active (%.1f/%.1fs) - injecting test signal", elapsed, TEST_SIGNAL_TIMEOUT_SECONDS)
                #         payload = generate_test_signal(args.base_url, args.session_id, fallback_expiry=int(args.expiry))
                #     else:
                #         LOGGER.info("test_signal: Timeout reached - test window closed")
                #         test_signal_active = False
                
                payload: Optional[Dict[str, Any]] = None
                signal_fetch_latency: Optional[float] = None

                # Fetch real signal (no test signal injection)
                fetch_started = time.time()
                payload = fetch_latest_signal(args.base_url, args.session_id)
                signal_fetch_latency = max(0.0, time.time() - fetch_started)

                now = time.time()
                tracker_fetch_latency = None
                if last_tracker_snapshot is None or (now - last_tracker_fetch_ts) >= 1.0:
                    tracker_fetch_started = time.time()
                    fetched_tracker_snapshot: Optional[Dict[str, Any]] = fetch_tracker_session_snapshot(args.base_url, args.session_id)
                    last_tracker_snapshot = fetched_tracker_snapshot
                    tracker_fetch_latency = max(0.0, time.time() - tracker_fetch_started)
                    last_tracker_fetch_ts = now
                tracker_snapshot: Optional[Dict[str, Any]] = last_tracker_snapshot
                if payload is not None and tracker_snapshot is not None:
                    payload = _signal_with_tracker_context(payload, tracker_snapshot)
                if payload is not None:
                    payload["_fetch_latency_sec"] = signal_fetch_latency
                if tracker_snapshot is not None and tracker_fetch_latency is not None:
                    tracker_snapshot["_fetch_latency_sec"] = tracker_fetch_latency
                signal_payload: Optional[Dict[str, Any]]
                signal_payload = payload
                tracker_payload = tracker_snapshot
                if cooldown_expiry_ts > 0.0 and now >= cooldown_expiry_ts:
                    cooldown_expiry_ts = 0.0
                    trades_in_window = 0
                    window_start_ts = now
                    last_signal_id = ""
                    LOGGER.info("safety: trade window cooldown completed; entries re-enabled")
                elif trades_in_window > 0 and window_start_ts > 0.0 and (now - window_start_ts) >= WINDOW_DURATION_SECONDS and cooldown_expiry_ts <= 0.0:
                    trades_in_window = 0
                    window_start_ts = now
                    LOGGER.info("safety: trade window reset after %d seconds", WINDOW_DURATION_SECONDS)

                cooldown_remaining_seconds = max(0, int(cooldown_expiry_ts - now)) if cooldown_expiry_ts > 0.0 else 0
                status_box.update(signal_payload, tracker_payload, cooldown_remaining_seconds)

                payload_data: Dict[str, Any] = {}
                if payload is not None:
                    payload_data = payload

                    # Prefer tracker countdown when available, but never gate
                    # execution on a slow snapshot tick. The tracker now studies
                    # in seconds; freshness, explicit actionability, expiry, and
                    # the two-poll stability guard decide whether to click.
                    countdown_val: Optional[int] = None
                    if isinstance(tracker_payload, dict):
                        try:
                            countdown_val = _extract_countdown_seconds(tracker_payload)
                        except Exception:
                            countdown_val = None
                    if countdown_val is None:
                        try:
                            countdown_val = _extract_countdown_seconds(payload_data)
                        except Exception:
                            countdown_val = None

                    if countdown_val is not None:
                        payload_data.setdefault("countdown_seconds", int(countdown_val))

                    if not startup_gate_open:
                        identity_key = _payload_identity_key(payload_data)
                        now = time.time()

                        if not startup_identity_key:
                            if identity_key:
                                startup_identity_key = identity_key
                                LOGGER.info(
                                    "strict startup gate armed: baseline signal identity captured (%s). Waiting for a newly changed signal before execution.",
                                    startup_identity_key,
                                )
                            elif now - last_waiting_log_ts >= 2.0:
                                LOGGER.info("strict startup gate waiting: no signal identity available yet")
                                last_waiting_log_ts = now
                            time.sleep(float(args.poll))
                            continue

                        if identity_key == startup_identity_key:
                            if now - last_waiting_log_ts >= 2.0:
                                kernel_state = payload_data.get("decision_kernel_state") or payload_data.get("signal_armed_state") or payload_data.get("kernel_state") or "?"
                                LOGGER.info(
                                    "strict startup gate waiting: baseline=%s kernel=%s action=%s",
                                    startup_identity_key,
                                    kernel_state,
                                    payload_data.get("action", "?"),
                                )
                                last_waiting_log_ts = now
                            time.sleep(float(args.poll))
                            continue

                        if not startup_gate_open:
                            startup_gate_open = True
                            LOGGER.info(
                                "strict startup gate opened: new signal identity detected (baseline=%s, current=%s)",
                                startup_identity_key,
                                identity_key or "<empty>",
                            )

                    if bool(getattr(args, "require_fresh_signal", True)) and not _payload_is_fresh(payload_data, float(getattr(args, "max_signal_age", 20.0))):
                        now = time.time()
                        if now - last_waiting_log_ts >= 2.0:
                            age_text = "unknown"
                            age = _payload_age_seconds(payload_data)
                            if age is not None:
                                age_text = f"{age:.1f}s"
                            LOGGER.info(
                                "waiting for fresh Phoenix signal: status=%s stale=%s freshness=%s age=%s max_age=%.1fs",
                                payload_data.get("status", ""),
                                payload_data.get("stale", False),
                                payload_data.get("freshness_score", "n/a"),
                                age_text,
                                float(getattr(args, "max_signal_age", 20.0)),
                            )
                            last_waiting_log_ts = now
                        time.sleep(float(args.poll))
                        continue

                parsed: Optional[Tuple[str, int, str, str, str, Any, str, Any]] = parse_trade_signal(payload_data)

                # CANDLE NOTATION: Parse and convert if present
                side = ""
                expiry = 0
                signal_id = ""
                expiry_source = ""
                expiry_raw_field = "n/a"
                expiry_raw_value: Any = None
                side_raw_field = "n/a"
                side_raw_value: Any = None
                if parsed is not None:
                    side, expiry, signal_id, expiry_source, expiry_raw_field, expiry_raw_value, side_raw_field, side_raw_value = parsed
                    payload_timeframe = payload_data.get("focus_timeframe") or payload_data.get("timeframe") or payload_data.get("active_timeframe") or "M5"
                    candle_notation = payload_data.get("expiry_notation") or payload_data.get("candle_notation") or payload_data.get("notation")
                    if isinstance(candle_notation, str) and candle_notation.strip().upper().endswith('C'):
                        candle_seconds = _candle_notation_to_seconds(candle_notation, payload_timeframe)
                        if candle_seconds and candle_seconds > 0:
                            expiry = candle_seconds
                            expiry_source = f"candle_notation({candle_notation})"
                            expiry_raw_field = "candle_notation"
                            expiry_raw_value = candle_notation
                            LOGGER.info("candle-parsed: updated expiry to %ds from notation %s", expiry, candle_notation)
                    parsed = (side, expiry, signal_id, expiry_source, expiry_raw_field, expiry_raw_value, side_raw_field, side_raw_value)

                if parsed is not None:
                    last_non_actionable_status = ""
                    side, expiry, signal_id, expiry_source, expiry_raw_field, expiry_raw_value, side_raw_field, side_raw_value = parsed
                    if not signal_id:
                        LOGGER.warning("strict signal rejected after parse because identity is missing")
                        time.sleep(float(args.poll))
                        continue
                    # Populate resolved expiry context
                    payload_data["expiry_seconds"] = expiry
                    payload_data["_resolved_expiry_source"] = expiry_source
                    payload_data["_resolved_expiry_raw_field"] = expiry_raw_field
                    payload_data["_resolved_expiry_raw_value"] = expiry_raw_value

                    # Choose an adaptive expiry based on Phoenix hints and kernel state
                    try:
                        chosen = int(_choose_adaptive_expiry(payload_data, int(expiry or 0), args))
                        base_exp = int(expiry or 0)
                        if chosen != base_exp:
                            LOGGER.info(
                                "adaptive expiry: adjusted %ds -> %ds based on Phoenix signals",
                                base_exp,
                                chosen,
                            )
                            expiry = int(chosen)
                            payload_data["expiry_seconds"] = expiry
                            payload_data["_resolved_expiry_source"] = payload_data.get("_resolved_expiry_source") or "adaptive_selector"
                            payload_data["_resolved_expiry_raw_field"] = payload_data.get("_resolved_expiry_raw_field") or "adaptive_selector"
                            payload_data["_resolved_expiry_raw_value"] = payload_data.get("_resolved_expiry_raw_value") or "adaptive_selector"
                    except Exception as exc:
                        LOGGER.warning("adaptive expiry selection failed: %s", exc)
                    payload_data["_resolved_side_raw_field"] = side_raw_field
                    payload_data["_resolved_side_raw_value"] = side_raw_value
                    cooldown_remaining_seconds = max(0, int(cooldown_expiry_ts - now)) if cooldown_expiry_ts > 0.0 else 0
                    status_box.update(payload_data, tracker_snapshot, cooldown_remaining_seconds)

                    if cooldown_remaining_seconds > 0:
                        if now - last_cooldown_log_ts >= 2.0:
                            LOGGER.info(
                                "safety: trade limit reached (%d/%d); waiting %ds before next entry",
                                trades_in_window,
                                MAX_TRADES_PER_WINDOW,
                                cooldown_remaining_seconds,
                            )
                            last_cooldown_log_ts = now
                        time.sleep(float(args.poll))
                        continue

                    if trades_in_window >= MAX_TRADES_PER_WINDOW:
                        cooldown_expiry_ts = now + WINDOW_DURATION_SECONDS
                        cooldown_remaining_seconds = WINDOW_DURATION_SECONDS
                        last_cooldown_log_ts = now
                        LOGGER.warning(
                            "safety: trade limit reached (%d/%d); locking entries for %d seconds",
                            trades_in_window,
                            MAX_TRADES_PER_WINDOW,
                            WINDOW_DURATION_SECONDS,
                        )
                        status_box.update(payload_data, tracker_snapshot, cooldown_remaining_seconds)
                        time.sleep(float(args.poll))
                        continue

                    now = time.time()
                    if signal_id == last_signal_id:
                        LOGGER.debug("Signal unchanged (%s), no new click", signal_id)
                    elif now - last_shot_ts < float(args.cooldown):
                        LOGGER.debug("Cooldown active (%.2fs left), skip %s", float(args.cooldown) - (now - last_shot_ts), signal_id)
                    else:
                        # HARDENING: Expiry-Stability Guard
                        # For test signals, skip stability check; for real signals, require 2-poll confirmation
                        is_test_signal = bool(payload_data.get("test_mode", False))
                        
                        if is_test_signal:
                            # Test signal bypasses stability check for immediate execution
                            is_stable = True
                            LOGGER.info("test_signal: Stability check BYPASSED for test signal (immediate execution)")
                        else:
                            # Real signals require 2-poll confirmation
                            with stable_signal_lock:
                                is_stable = False
                                if (last_stable_signal["signal_id"] == signal_id and 
                                    int(last_stable_signal["expiry_seconds"]) == expiry):
                                    last_stable_signal["polls_matched"] = int(last_stable_signal["polls_matched"]) + 1
                                    if int(last_stable_signal["polls_matched"]) >= 2:
                                        is_stable = True
                                    LOGGER.info("stability: signal %s expiry %ss matched %d/2 polls", 
                                               signal_id, expiry, int(last_stable_signal["polls_matched"]))
                                else:
                                    last_stable_signal["signal_id"] = signal_id
                                    last_stable_signal["expiry_seconds"] = expiry
                                    last_stable_signal["polls_matched"] = 1
                                    LOGGER.info("stability: new signal %s expiry %ss (reset to 1/2 polls)", signal_id, expiry)
                        
                        if not is_stable:
                            LOGGER.info("stability: deferring execution pending 2-poll confirmation for %s", signal_id)
                        else:
                            # Check if automatic trigger is enabled before executing
                            with automatic_trigger_lock:
                                trigger_enabled = automatic_trigger_enabled
                            
                            if not trigger_enabled:
                                LOGGER.info("⏸️  Automatic trigger DISABLED: Signal %s received but not executed", signal_id)
                            else:
                                # All confirmed Phoenix trades execute without
                                # an artificial startup skip.
                                # if ignore_first_phoenix_trade:
                                #     ignore_first_phoenix_trade = False
                                #     LOGGER.warning(
                                #         "safety: ignoring first Phoenix trade %s @ %ss; next trade will be allowed",
                                #         signal_id,
                                #         expiry,
                                #     )
                                # else:
                                # Signal mode is kernel-driven: always prefer parsed signal expiry.
                                if expiry <= 0:
                                    LOGGER.error("strict signal rejected because expiry resolved to %s", expiry)
                                    time.sleep(float(args.poll))
                                    continue
                                chosen_expiry = expiry
                                ok = execute_trade(hwnd, boxes, side, chosen_expiry, args.amount)
                                if ok:
                                    last_signal_id = signal_id
                                    last_shot_ts = now
                                    trades_executed_count += 1
                                    trades_in_window += 1
                                    if trades_in_window == 1:
                                        window_start_ts = now
                                    if trades_in_window >= MAX_TRADES_PER_WINDOW:
                                        cooldown_expiry_ts = now + WINDOW_DURATION_SECONDS
                                        last_cooldown_log_ts = now
                                        LOGGER.warning(
                                            "safety: trade window limit reached (%d/%d); pausing entries for %d seconds",
                                            trades_in_window,
                                            MAX_TRADES_PER_WINDOW,
                                            WINDOW_DURATION_SECONDS,
                                        )
                                    
                                    # DISABLED: Test signal execution marking commented out
                                    # Mark test signal as executed
                                    # if is_test_signal:
                                    #     test_signal_executed = True
                                    #     LOGGER.info("✅ TEST SIGNAL EXECUTED - System ready for live trading!")
                                    
                                    # HARDENING: Safety Wait Mechanism
                                    # Every N trades, pause and allow user to change pair
                                    if trades_executed_count > 0 and trades_executed_count % SAFETY_WAIT_INTERVAL == 0:
                                        pause_seconds = _random_safety_pause_seconds()
                                        LOGGER.info("\n" + "="*60)
                                        LOGGER.info("⚠️  SAFETY CHECKPOINT: %d trades completed", trades_executed_count)
                                        LOGGER.info("Automatic execution will pause for %ds, then resume listening.", pause_seconds)
                                        LOGGER.info("="*60 + "\n")
                                        _pause_automatic_trigger_for(pause_seconds, source="safety checkpoint")
                else:
                    status_key = "|".join(
                        str(payload_data.get(key, "") or "")
                        for key in ("signal_id", "status", "action", "execution_action", "actionable")
                    )
                    if status_key != last_non_actionable_status:
                        last_non_actionable_status = status_key
                        LOGGER.info(
                            "Latest signal not executable: status=%s action=%s execution_action=%s actionable=%s",
                            payload_data.get("status", ""),
                            payload_data.get("action", ""),
                            payload_data.get("execution_action", ""),
                            payload_data.get("actionable", False),
                        )
                time.sleep(float(args.poll))
            except KeyboardInterrupt:
                LOGGER.info("Signal loop interrupted by user")
                break
            except Exception as exc:
                LOGGER.error("Unexpected error in signal loop: %s", exc)
                time.sleep(float(args.poll))
    finally:
        status_box.stop()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python "808 Shooter.py"',
        description="Standalone real-click Pocket Option executor with optional architecture signal feed.",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    windows = sub.add_parser("list-windows", help="List visible top-level windows to pick a target query.")
    windows.add_argument("--contains", default=None, help="Filter titles containing this text")
    windows.set_defaults(mode="list-windows")

    calibrate = sub.add_parser("calibrate", help="Calibrate click points by hovering each UI box.")
    calibrate.add_argument("--window-query", default=None, help="Title substring to select broker window")
    calibrate.set_defaults(mode="calibrate")

    preview = sub.add_parser("preview", help="Show resolved box coordinates for current window.")
    preview.add_argument("--window-query", default=None, help="Title substring to select broker window")
    preview.set_defaults(mode="preview")

    manual = sub.add_parser("manual", help="Manual one-shot execution.")
    manual.add_argument("side", choices=["buy", "sell"], help="Trade direction")
    manual.add_argument("expiry", type=int, help="Expiry seconds")
    manual.add_argument("--amount", type=int, default=5, help="Order amount")
    manual.add_argument("--window-query", default=None, help="Title substring to select broker window")
    manual.set_defaults(mode="manual")

    signal = sub.add_parser("signal", help="Follow architecture signal and auto-click actionable trades.")
    signal.add_argument("--session-id", required=True, help="Observer session id")
    signal.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Mobile API base URL")
    signal.add_argument("--poll", type=float, default=0.10, help="Signal poll interval seconds")
    signal.add_argument("--cooldown", type=float, default=45.0, help="Min seconds between clicks")
    signal.add_argument("--expiry", type=int, default=DEFAULT_EXPIRY_FALLBACK_SECONDS, help="Fallback/manual expiry")
    signal.add_argument(
        "--max-signal-age",
        type=float,
        default=3.0,
        help="Maximum signal age in seconds allowed for execution when waiting for fresh signals",
    )
    signal.add_argument(
        "--allow-stale-signal",
        dest="require_fresh_signal",
        action="store_false",
        help="Allow execution on stale signals (not recommended)",
    )
    signal.add_argument(
        "--disable-kernel-trigger-fallback",
        dest="kernel_trigger_fallback",
        action="store_false",
        help="Legacy option retained for compatibility; strict execution keeps kernel fallback disabled",
    )
    signal.add_argument(
        "--disable-strict-new-signal-mode",
        dest="strict_new_signal_mode",
        action="store_false",
        help="Allow execution without waiting for a changed signal identity after startup",
    )
    signal.add_argument(
        "--disable-auto-arm",
        dest="auto_arm_collaboration",
        action="store_false",
        help="Disable auto-arm collaboration mode (snapping to Phoenix countdown on startup)",
    )
    signal.add_argument(
        "--hold-bias-fallback",
        action="store_true",
        help="Legacy option retained for compatibility; ignored while strict execution is enabled",
    )
    signal.add_argument("--use-signal-expiry", action="store_true", help="Use expiry from signal payload when present")
    signal.add_argument("--amount", type=int, default=5, help="Order amount")
    signal.add_argument("--window-query", default=None, help="Title substring to select broker window")
    signal.add_argument("--broker-url", default=DEFAULT_BROKER_URL, help="Broker URL to open automatically when no broker window is found")
    signal.add_argument(
        "--no-auto-open",
        dest="auto_open_broker",
        action="store_false",
        help="Do not open the broker URL automatically before signal mode starts",
    )
    signal.add_argument(
        "--clear-phoenix-cache",
        dest="clear_phoenix_cache",
        action="store_true",
        help="Backup and clear common Phoenix/local cache files in the workspace data/ folder before signal mode starts",
    )
    signal.add_argument(
        "--test-signal",
        dest="test_signal",
        action="store_true",
        help="Inject a startup test entry based on Phoenix's current BUY/SELL bias and countdown (default)",
    )
    signal.add_argument(
        "--no-test-signal",
        dest="test_signal",
        action="store_false",
        help="Skip the startup Phoenix-bias test entry",
    )
    signal.add_argument(
        "--adaptive-verbose",
        dest="adaptive_verbose",
        action="store_true",
        help="Enable verbose diagnostic logs for adaptive expiry selection",
    )
    signal.add_argument(
        "--kernel-candles",
        type=int,
        default=20,
        help="Number of candles to map kernel probability hints to (default: 20)",
    )
    signal.add_argument(
        "--adaptive-test",
        dest="adaptive_test",
        action="store_true",
        help="Run adaptive-expiry test scenarios and exit",
    )
    signal.set_defaults(auto_open_broker=True)
    signal.set_defaults(require_fresh_signal=True)
    # Disable kernel-trigger fallback by default to avoid implicit execution
    # decisions based on planner/kernel hints unless explicitly enabled.
    signal.set_defaults(kernel_trigger_fallback=False)
    signal.set_defaults(strict_new_signal_mode=False)  # Disabled by default: Phoenix doesn't change signal_id on state transitions
    signal.set_defaults(auto_arm_collaboration=False)
    signal.set_defaults(test_signal=False)
    signal.set_defaults(mode="signal")

    return parser


def main() -> int:
    set_dpi_awareness()
    parser = build_parser()
    args = parser.parse_args()

    if args.mode == "list-windows":
        windows = list_visible_windows(args.contains)
        if not windows:
            print("No matching visible windows found.")
            return 1
        print("Visible windows:")
        for hwnd, title, class_name in windows:
            print(f"  HWND={hwnd:<10} class={class_name:<24} title={title}")
        return 0

    if args.mode == "calibrate":
        hwnd = find_pocket_option_window(getattr(args, "window_query", None))
        if hwnd is None:
            return 2
        if not activate_window(hwnd):
            return 2
        calibrate_boxes(hwnd)
        return 0

    if args.mode == "preview":
        hwnd = find_pocket_option_window(getattr(args, "window_query", None))
        if hwnd is None:
            return 2
        if not activate_window(hwnd):
            return 2
        show_box_preview(hwnd, load_boxes())
        return 0

    if args.mode == "manual":
        return run_manual(args)

    if args.mode == "signal":
        return run_signal_loop(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped by user")
        sys.exit(0)
