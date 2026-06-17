#!/usr/bin/env python3
# pyright: reportUnusedFunction=false, reportUnusedVariable=false, reportGeneralTypeIssues=false
"""
Shooter - Standalone Pocket Option live click executor.

This script is isolated from the main execution path but can still consume
Model Council execution packets from PhoenixGuard:
GET /v1/mobile/model-council/sessions/{sessionId}/execution/latest

Modes:
- Manual one-shot: BUY/SELL + expiry
- Signal-follow loop: poll Model Council and execute only EXECUTABLE V3 packets

It performs real UI clicks on the Pocket Option order panel.
"""

import argparse
import ctypes
from dataclasses import replace
from functools import lru_cache
import hashlib
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import math
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, cast

import pyautogui

from phoenixguard.execution import shooter_modes
from phoenixguard.execution.execution_constitution import evaluate_execution_constitution
from phoenixguard.execution.execution_rehearsal import rehearse_execution
from phoenixguard.execution.floating_state_reducer import build_floating_state
from phoenixguard.execution.packet_v3 import validate_execution_packet_v3
from phoenixguard.execution.sequence_context import resolve_sequence_context
from phoenixguard.execution.shooter_action_sequencer import (
    ActionEvidenceRecorder,
    ActionSequenceResult,
    BrokerTimingProfile,
    LowLevelActionAdapter,
    ShooterActionSequencerV2,
    write_live_behavior_validation_report,
)

try:
    import tkinter as tk
    from tkinter import ttk
    has_tkinter = True
except Exception:
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    has_tkinter = False

try:
    from PIL import Image, ImageTk
    has_pillow = True
except Exception:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]
    has_pillow = False

keyboard: Any | None = None
pytesseract: Any | None = None
LOW_LEVEL_ACTION_ADAPTER = LowLevelActionAdapter(pyautogui)

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


def _configure_tesseract_runtime() -> None:
    if not has_ocr or pytesseract is None:
        return
    candidates = [
        os.getenv("TESSERACT_CMD", ""),
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        str(Path(os.getenv("ProgramFiles", "")) / "Tesseract-OCR" / "tesseract.exe"),
        str(Path(os.getenv("ProgramFiles(x86)", "")) / "Tesseract-OCR" / "tesseract.exe"),
    ]
    for candidate in candidates:
        path = Path(str(candidate).strip())
        if path.is_file():
            try:
                pytesseract.pytesseract.tesseract_cmd = str(path)
                os.environ.setdefault("TESSERACT_CMD", str(path))
            except Exception:
                pass
            return


_configure_tesseract_runtime()

# Disable PyAutoGUI failsafe corner abort for smoother automation.
pyautogui.FAILSAFE = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("shooter")
_SHOOTER_RUNTIME_DIR = Path(__file__).resolve().parent / ".codex_runtime"
_SHOOTER_HANDSHAKE_PATH = _SHOOTER_RUNTIME_DIR / "shooter_handshake.json"
_last_action_sequence_result: Optional[ActionSequenceResult] = None
_confirmed_expiry_cache_lock = threading.Lock()
_confirmed_expiry_cache: Dict[str, Any] = {}
# File logger for detailed debugging (rotates to limit disk usage)
try:
    _file_handler = RotatingFileHandler("shooter_debug.log", maxBytes=2_000_000, backupCount=5)
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    LOGGER.addHandler(_file_handler)
    # Keep console INFO but enable DEBUG for file
    LOGGER.setLevel(logging.DEBUG)
except Exception:
    # Best-effort: if file handler can't be created, continue with console logging
    pass

BOXES_FILE = Path("808_shooter_boxes.json")
CALIBRATION_MANIFEST_FILE = Path("user_calibration_manifest.json")
DEFAULT_BROKER_TIMING_PROFILE_FILE = Path("config") / "shooter_broker_timing_profile.json"
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

# Strict backend confirmation is authoritative; execute on the first fresh
# accepted signal instead of adding a second poll delay in the shooter.
REQUIRED_STABLE_SIGNAL_POLLS = 1
# Expiry-stability state is retained only to ignore repeated signal ids.
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
DEFAULT_SIGNAL_POLL_SECONDS = 0.05
DEFAULT_LIVE_DISABLED_SIGNAL_POLL_SECONDS = 0.50
DEFAULT_MAX_SIGNAL_AGE_SECONDS = 8.0
DEFAULT_MODEL_COUNCIL_FETCH_TIMEOUT_SECONDS = 12.0
DEFAULT_LIVE_API_FETCH_TIMEOUT_SECONDS = 0.75
DEFAULT_PRE_CLICK_CONFIRMATION_TIMEOUT_SECONDS = 1.0
DEFAULT_TRACKER_SESSION_FETCH_INTERVAL_SECONDS = 2.0
DEFAULT_EXECUTION_PACKET_FETCH_INTERVAL_SECONDS = 0.75
DEFAULT_ENDPOINT_PACKET_FETCH_TIMEOUT_SECONDS = 0.75
DEFAULT_STUDY_PACKET_FETCH_INTERVAL_SECONDS = 12.0
DEFAULT_STUDY_ENDPOINT_PACKET_FETCH_TIMEOUT_SECONDS = 0.25
DEFAULT_FLOATING_PREVIEW_INTERVAL_SECONDS = 2.0
STUDY_PACKET_FALLBACK_TTL_SECONDS = DEFAULT_MAX_SIGNAL_AGE_SECONDS
SAFETY_LOCKOUT_SECONDS = 20 * 60
DEFAULT_TRADE_COOLDOWN_SECONDS = SAFETY_LOCKOUT_SECONDS

# Memory bank paths and similarity defaults
MEMORY_BANK_DIR = Path("memory_bank")
MEMORY_MATCH_WINDOW = 10  # number of candles to consider for similarity
# Tuned default confidence based on historical memory_similarity distribution
# Historical pass recommends a lower threshold because typical stored similarities
# live around 0.12-0.26; set conservative min to allow valid patterns while
# preventing obviously unrelated signals.
MIN_CONFIDENCE_TO_EXECUTE = 0.20  # 0.0-1.0


def _safe_import_numpy() -> Optional[object]:
    try:
        import numpy as np  # type: ignore

        return np
    except Exception:
        return None


def load_memory_index() -> Optional[Dict[str, Any]]:
    """Load memory bank index if available. Returns dict with 'vecs' (numpy array) and 'id_map'."""
    try:
        id_map_path = MEMORY_BANK_DIR / "index" / "id_map.json"
        vecs_path = MEMORY_BANK_DIR / "index" / "numpy_vecs.npy"
        if not id_map_path.exists() or not vecs_path.exists():
            LOGGER.debug("memory: index missing")
            return None
        np = _safe_import_numpy()
        if np is None:
            LOGGER.debug("memory: numpy not available for similarity checks")
            return None
        import json

        id_map = json.loads(id_map_path.read_text(encoding="utf-8"))
        vecs = np.load(str(vecs_path), allow_pickle=False)
        return {"id_map": id_map, "vecs": vecs, "np": np}
    except Exception as exc:
        LOGGER.debug("memory: failed to load index: %s", exc)
        return None


def _feature_vector_from_payload(payload: Dict[str, Any], np: Optional[object] = None) -> Optional[Any]:
    """Create a small numeric feature vector from payload for similarity checking.

    Returns numpy array if `np` is provided, else a simple tuple.
    """
    try:
        kernel = payload.get("decision_kernel") or {}
        p1 = float(_coerce_nonnegative_seconds(kernel.get("p_trigger_next_1")) or 0.0)
        p3 = float(_coerce_nonnegative_seconds(kernel.get("p_trigger_next_3")) or 0.0)
        close_pos = float(_coerce_nonnegative_seconds(payload.get("close_position")) or 0.0)
        scenario_prob = 0.0
        scenario = payload.get("scenario_analysis") or {}
        if isinstance(scenario, dict):
            top = scenario.get("top_scenario")
            if isinstance(top, dict):
                try:
                    scenario_prob = float(top.get("probability") or 0.0)
                except Exception:
                    scenario_prob = 0.0

        micro = str(
            payload.get("micro_structure_event")
            or (payload.get("tracking_summary") or {}).get("micro_structure_event")
            or ""
        ).strip().lower()
        # map string micro-event categories to small numeric bucket
        micro_map = {"": 0.0, "rejection": 0.5, "pullback": 0.7}
        micro_val = micro_map.get(micro.split("_")[0], 0.2)

        vec = (p1, p3, close_pos, scenario_prob, micro_val)
        if np is not None:
            return np.asarray(vec, dtype=float)
        return vec
    except Exception:
        return None


def memory_confidence_for_payload(payload: Dict[str, Any]) -> float:
    """Return a confidence score (0..1) comparing payload to memory bank examples.

    If memory index missing, returns a conservative 0.5 for neutral.
    """
    try:
        idx = load_memory_index()
        if not idx:
            return 0.5
        np = idx.get("np")
        if np is None:
            return 0.5
        vecs = idx.get("vecs")
        if vecs is None:
            return 0.5
        cur = _feature_vector_from_payload(payload, np)
        if cur is None:
            return 0.5
        # Normalize and compute cosine similarity to all stored vecs
        try:
            # prevent zero vectors
            def _norm(a):
                n = np.linalg.norm(a)
                return a / (n + 1e-9)

            curn = _norm(cur)
            stored = vecs.astype(float)
            # ensure 2D
            if stored.ndim == 1:
                stored = stored.reshape(1, -1)
            stored_n = stored / (np.linalg.norm(stored, axis=1, keepdims=True) + 1e-9)
            sims = stored_n.dot(curn)
            best = float(np.max(sims)) if sims.size else 0.0
            # map similarity (-1..1) to confidence 0..1
            conf = max(0.0, min(1.0, (best + 1.0) / 2.0))
            return conf
        except Exception:
            return 0.5
    except Exception:
        return 0.5


def log_decision(payload: Dict[str, Any], decision: str, reason: str, confidence: float) -> None:
    try:
        out = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "signal_id": payload.get("signal_id"),
            "decision": decision,
            "reason": reason,
            "confidence": float(confidence),
        }
        log_path = MEMORY_BANK_DIR / "decisions.log"
        MEMORY_BANK_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(out) + "\n")
    except Exception:
        LOGGER.debug("memory: failed to write decision log")

# Enforce strict execution behavior: when True, do not use implicit fallbacks
# for missing expiry or kernel/hold-trigger fallbacks. This makes the shooter
# refuse to act on signals that lack explicit actionable fields so the system
# must be fully tightened and calibrated before deployment.
ENFORCE_STRICT_EXECUTION = True
AUTHORITATIVE_SIGNAL_ENDPOINT = "model_council"
STRICT_EXECUTION_EXPIRY_FIELDS = ("expiry_seconds", "expiry_sec", "required_seconds")
STRICT_SWING_MIN_TARGET_CANDLES = 10
STRICT_SIGNAL_TIMEFRAME_SECONDS = {
    "M1": 60,
    "M3": 180,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
}

ENTRY_LOCATION_BUY_MAX_CLOSE_POSITION = 0.72
ENTRY_LOCATION_BUY_STRETCH_CLOSE_POSITION = 0.86
ENTRY_LOCATION_SELL_MIN_CLOSE_POSITION = 0.28
ENTRY_LOCATION_SELL_STRETCH_CLOSE_POSITION = 0.14
ENTRY_LOCATION_HISTORY_MIN_SAMPLE = 6
ENTRY_LOCATION_BUY_HISTORY_MAX_POSITION = 0.74
ENTRY_LOCATION_BUY_HISTORY_STRETCH_POSITION = 0.88
ENTRY_LOCATION_SELL_HISTORY_MIN_POSITION = 0.26
ENTRY_LOCATION_SELL_HISTORY_STRETCH_POSITION = 0.12
ENTRY_LOCATION_SIGNIFICANT_ENTRY_MIN_SCORE = 0.38
ENTRY_LOCATION_ENTRY_AREA_NEAR_DISTANCE = 0.24
ENTRY_LOCATION_BUY_FAVORABLE_GLOBAL_MAX_POSITION = 0.38
ENTRY_LOCATION_SELL_FAVORABLE_GLOBAL_MIN_POSITION = 0.62
ENTRY_LOCATION_CURRENT_FLOW_MIN_P1 = 0.78
ENTRY_LOCATION_CURRENT_FLOW_MIN_P3 = 0.88
ENTRY_LOCATION_CURRENT_FLOW_MIN_TARGET = 0.64
ENTRY_LOCATION_CURRENT_FLOW_MAX_EXPIRE = 0.35
ENTRY_LOCATION_CURRENT_FLOW_MIN_ALIGNMENT = 4

ENTRY_LOCATION_PULLBACK_EVENTS = {
    "bullish_pullback_into_zone",
    "bearish_pullback_into_zone",
    "bullish_rejection",
    "bearish_rejection",
}

# Test signal configuration
TEST_SIGNAL_EXPIRY_SECONDS = 30
TEST_SIGNAL_TIMEOUT_SECONDS = 30
TEST_SIGNAL_POLL_INTERVAL = 0.5
DEFAULT_STARTUP_PRIME_EXPIRY_SECONDS = 600

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
    "signal_age_sec",
    "pipeline_latency_sec",
    "capture_count",
    "state_version",
    "decision_version",
    "decision_valid_until_epoch",
    "last_capture_epoch",
    "last_capture_at",
    "published_epoch",
    "published_at",
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


def _coerce_finite_float(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _clip_unit_float(raw: Any) -> Optional[float]:
    value = _coerce_finite_float(raw)
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def _position_in_price_series(values: Sequence[float], *, window: Optional[int] = None) -> Optional[float]:
    selected = list(values[-window:]) if window and window > 0 else list(values)
    clean = [float(value) for value in selected if math.isfinite(float(value))]
    if not clean:
        return None
    low = min(clean)
    high = max(clean)
    if high - low <= 1e-9:
        return 0.5
    return max(0.0, min(1.0, float((clean[-1] - low) / (high - low))))


def _extract_price_proxy_series_from_tracking(tracking_summary: Mapping[str, Any]) -> List[float]:
    rows_any = tracking_summary.get("tracked_candles", [])
    if not isinstance(rows_any, Sequence) or isinstance(rows_any, (str, bytes, bytearray)):
        return []
    values: List[float] = []
    for row in rows_any:
        if not isinstance(row, Mapping):
            continue
        for key in ("price_proxy", "close_proxy", "c", "close"):
            if key not in row:
                continue
            parsed = _coerce_finite_float(row.get(key))
            if parsed is not None:
                values.append(float(parsed))
                break
    if not values:
        parsed_latest = _coerce_finite_float(tracking_summary.get("latest_price_proxy"))
        if parsed_latest is not None:
            values.append(float(parsed_latest))
    return values


def _price_proxy_values_from_tracking_row(row: Mapping[str, Any]) -> List[float]:
    values: List[float] = []
    for key in (
        "high_proxy",
        "low_proxy",
        "open_proxy",
        "close_proxy",
        "price_proxy",
        "h",
        "l",
        "o",
        "c",
        "high",
        "low",
        "open",
        "close",
    ):
        if key not in row:
            continue
        parsed = _coerce_finite_float(row.get(key))
        if parsed is not None:
            values.append(float(parsed))
    return values


def _extract_price_proxy_range_values_from_tracking(
    tracking_summary: Mapping[str, Any],
    *,
    window: Optional[int] = None,
) -> List[float]:
    rows_any = tracking_summary.get("tracked_candles", [])
    if not isinstance(rows_any, Sequence) or isinstance(rows_any, (str, bytes, bytearray)):
        return []
    rows = [row for row in rows_any if isinstance(row, Mapping)]
    selected = rows[-window:] if window and window > 0 else rows
    values: List[float] = []
    for row in selected:
        values.extend(_price_proxy_values_from_tracking_row(cast(Mapping[str, Any], row)))
    if not values:
        close_values = _extract_price_proxy_series_from_tracking(tracking_summary)
        values.extend(close_values[-window:] if window and window > 0 else close_values)
    return [float(value) for value in values if math.isfinite(float(value))]


def _position_value_in_price_values(value: Optional[float], values: Sequence[float]) -> Optional[float]:
    selected = [float(item) for item in values if math.isfinite(float(item))]
    if value is None or not math.isfinite(float(value)) or not selected:
        return None
    low = min(selected)
    high = max(selected)
    if high - low <= 1e-9:
        return 0.5
    return max(0.0, min(1.0, (float(value) - low) / (high - low)))


def _entry_history_position_context(payload: Dict[str, Any], tracking_summary: Mapping[str, Any]) -> Dict[str, Any]:
    """Return price position inside the visible studied history, where 0=low and 1=high."""
    result: Dict[str, Any] = {}
    timing_sources = (payload.get("execution_timing"), tracking_summary.get("execution_timing"))
    for source in timing_sources:
        if not isinstance(source, dict):
            continue
        price_position = source.get("price_position")
        if not isinstance(price_position, dict):
            continue
        for key in ("global_position", "local_position"):
            parsed = _clip_unit_float(price_position.get(key))
            if parsed is not None:
                result[key] = parsed
        sample_size_raw = price_position.get("sample_size")
        try:
            sample_size = int(sample_size_raw)
        except (TypeError, ValueError):
            sample_size = 0
        if sample_size > 0:
            result["sample_size"] = sample_size
        if "global_position" in result or "local_position" in result:
            return result

    series = _extract_price_proxy_series_from_tracking(tracking_summary)
    if not series:
        return result
    latest_price = _coerce_finite_float(tracking_summary.get("latest_price_proxy"))
    if latest_price is None:
        latest_price = _coerce_finite_float(payload.get("latest_price_proxy"))
    if latest_price is None and series:
        latest_price = float(series[-1])
    range_values = _extract_price_proxy_range_values_from_tracking(tracking_summary)
    local_range_values = _extract_price_proxy_range_values_from_tracking(tracking_summary, window=12)
    global_position = _position_value_in_price_values(latest_price, range_values) if range_values else _position_in_price_series(series)
    local_position = (
        _position_value_in_price_values(latest_price, local_range_values)
        if local_range_values
        else _position_in_price_series(series, window=12)
    )
    if global_position is not None:
        result["global_position"] = global_position
    if local_position is not None:
        result["local_position"] = local_position
    rows_any = tracking_summary.get("tracked_candles", [])
    tracked_count = len(rows_any) if isinstance(rows_any, Sequence) and not isinstance(rows_any, (str, bytes, bytearray)) else 0
    result["sample_size"] = max(len(series), tracked_count)
    if range_values:
        result["range_sample_size"] = len(range_values)
    return result


def _execution_timing_block_reason(payload: Dict[str, Any]) -> Optional[str]:
    tracking_summary = payload.get("tracking_summary")
    tracking_dict = tracking_summary if isinstance(tracking_summary, dict) else {}
    timing_sources = (payload.get("execution_timing"), tracking_dict.get("execution_timing"))
    for source in timing_sources:
        if not isinstance(source, dict):
            continue
        entry_allowed = source.get("entry_allowed")
        if entry_allowed is False or (isinstance(entry_allowed, str) and entry_allowed.strip().lower() in {"0", "false", "no", "off"}):
            candidate = _normalize_trade_side(source.get("candidate_side") or payload.get("candidate_action") or payload.get("action")) or "HOLD"
            if _timing_profile_current_flow_ready(source, candidate):
                continue
            timing_class = str(source.get("timing_class", "") or "timing_wait").strip()
            reason = str(source.get("block_reason", "") or "PhoenixGuard timing profile rejected this entry.").strip()
            return f"{candidate} {timing_class}: {reason}"
    return None


def _support_resistance_zones_for_entry_gate(payload: Dict[str, Any], tracking_summary: Mapping[str, Any]) -> List[Dict[str, Any]]:
    def _mapping(value: Any) -> Dict[str, Any]:
        return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}

    raw_sources: List[Any] = [
        tracking_summary.get("support_resistance_zones"),
        payload.get("support_resistance_zones"),
        _mapping(tracking_summary.get("support_resistance_context")).get("significant_zones"),
        _mapping(payload.get("support_resistance_context")).get("significant_zones"),
    ]
    tracking_smc = _mapping(tracking_summary.get("smart_money_context"))
    payload_smc = _mapping(payload.get("smart_money_context"))
    raw_sources.extend(
        [
            _mapping(tracking_smc.get("support_resistance")).get("significant_zones"),
            _mapping(payload_smc.get("support_resistance")).get("significant_zones"),
            tracking_smc.get("liquidity_pools"),
            payload_smc.get("liquidity_pools"),
        ]
    )

    zones: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str]] = set()
    for source in raw_sources:
        if not isinstance(source, Sequence) or isinstance(source, (str, bytes, bytearray)):
            continue
        for row in source:
            if not isinstance(row, Mapping):
                continue
            zone = dict(cast(Mapping[str, Any], row))
            role = str(zone.get("role", "") or "").strip().lower()
            label = str(zone.get("label", zone.get("key", "")) or "").strip()
            line_y = str(zone.get("line_y", "") or "").strip()
            identity = (role, label, line_y)
            if identity in seen:
                continue
            seen.add(identity)
            zones.append(zone)
    return zones


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


def _parse_visible_time_seconds(raw: Any) -> Optional[int]:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = (
        text.upper()
        .replace("O", "0")
        .replace("I", "1")
        .replace("L", "1")
        .replace("|", "1")
    )
    clock_match = re.search(r"(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})", normalized)
    if clock_match:
        hours, minutes, seconds = (int(clock_match.group(i)) for i in range(1, 4))
        return max(0, hours * 3600 + minutes * 60 + seconds)
    short_match = re.search(r"(\d{1,2})\D+(\d{1,2})", normalized)
    if short_match:
        minutes, seconds = int(short_match.group(1)), int(short_match.group(2))
        return max(0, minutes * 60 + seconds)
    digits = re.sub(r"\D+", "", normalized)
    if len(digits) >= 5:
        seconds = int(digits[-2:])
        minutes = int(digits[-4:-2])
        hours = int(digits[:-4] or "0")
        return max(0, hours * 3600 + minutes * 60 + seconds)
    if len(digits) in {3, 4}:
        seconds = int(digits[-2:])
        minutes = int(digits[:-2] or "0")
        return max(0, minutes * 60 + seconds)
    return None


def _payload_from_preferred_source(payload: Dict[str, Any], preferred: str) -> bool:
    """Return True when payload appears to originate from the preferred/stable source.

    Heuristics:
    - explicit `source` or `origin` equals preferred
    - `execution_controls.live_execution_enabled` is True
    - `execution_controls.execution_mode` is 'live'
    """
    try:
        if not payload or not preferred:
            return False
        controls = payload.get("execution_controls") or {}
        if isinstance(controls, dict):
            mode = str(controls.get("execution_mode", "") or "").lower()
            if controls.get("live_execution_enabled") is False or mode == "shadow":
                return False
            if controls.get("live_execution_enabled") is True:
                return True
            if mode == "live":
                return True
        src = payload.get("source") or payload.get("origin") or payload.get("signal_origin")
        if isinstance(src, str) and src == preferred:
            return True
    except Exception:
        return False
    return False


def _broker_execution_state_block_reason(payload: Dict[str, Any]) -> str:
    broker_state = payload.get("broker_execution_state")
    if not isinstance(broker_state, dict):
        return ""
    state = cast(Dict[str, Any], broker_state)
    status = str(state.get("status", "") or "").strip().lower()
    lane = str(state.get("lane", "") or "").strip().upper()
    side = _normalize_trade_side(state.get("side"))
    active_trade = state.get("active_trade")
    if isinstance(active_trade, dict) and active_trade:
        return "tracker is already monitoring an active trade"
    if status in {
        "disabled",
        "blocked",
        "cooldown",
        "risk_pause",
        "retry_wait",
        "monitoring",
        "error",
        "throttled",
        "shadow_ready",
        "clicked",
        "click_sent_unverified",
    }:
        return f"tracker broker state is {status}"
    if lane in {"NONE", "RISK_GATE", "TIMING_BLOCKED", "EMERGENCY_STOP"}:
        return f"tracker execution lane is {lane or 'not executable'}"
    if side is not None and side not in {"BUY", "SELL"}:
        return f"tracker broker side is {side}"
    return ""


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


def _strict_execution_expiry_field_candidates(payload: Dict[str, Any]) -> List[Tuple[str, Any]]:
    return [
        (field_name, payload.get(field_name))
        for field_name in STRICT_EXECUTION_EXPIRY_FIELDS
    ]


def _strict_signal_timeframe_seconds(raw: Any) -> Optional[int]:
    label = str(raw or "").strip().upper()
    if label not in STRICT_SIGNAL_TIMEFRAME_SECONDS:
        return None
    return int(STRICT_SIGNAL_TIMEFRAME_SECONDS[label])


def _strict_swing_execution_gate(payload: Dict[str, Any], side: str, expiry_seconds: int) -> Tuple[bool, str]:
    kernel_any = payload.get("decision_kernel")
    if not isinstance(kernel_any, dict):
        return False, "missing decision_kernel"
    kernel = cast(Dict[str, Any], kernel_any)
    trade_mode = str(kernel.get("trade_mode", "") or "").strip().upper()
    tracking_summary_any = payload.get("tracking_summary")
    tracking_summary = cast(Dict[str, Any], tracking_summary_any) if isinstance(tracking_summary_any, dict) else {}
    if not tracking_summary:
        execution_payload = payload.get("execution")
        if isinstance(execution_payload, Mapping):
            tracking_summary = cast(Dict[str, Any], execution_payload.get("tracking_summary")) if isinstance(execution_payload.get("tracking_summary"), dict) else {}
    if not tracking_summary:
        council_payload = payload.get("model_council")
        if isinstance(council_payload, Mapping):
            tracking_summary = cast(Dict[str, Any], council_payload.get("tracking_summary")) if isinstance(council_payload.get("tracking_summary"), dict) else {}
    broker_state_any = payload.get("broker_execution_state")
    broker_state = cast(Dict[str, Any], broker_state_any) if isinstance(broker_state_any, dict) else {}
    execution_lane = str(
        payload.get("execution_lane")
        or payload.get("lane")
        or broker_state.get("lane", "")
        or ""
    ).strip().upper()
    entry_state = str(payload.get("entry_state", tracking_summary.get("entry_state", "")) or "").strip().upper()
    execution_side = _normalize_trade_side(payload.get("execution_action", payload.get("action")))
    pullback_reload_ready = (
        trade_mode == "PULLBACK_WAIT"
        and entry_state in {"SNIPER_READY", "TRIGGER_READY"}
        and execution_side == side
        and bool(payload.get("actionable", False))
    )
    location_sniper_ready = (
        execution_lane == "LOCATION_SNIPER"
        and execution_side == side
        and bool(payload.get("actionable", False))
    )
    market_flow_ready = (
        execution_lane in {"LIVE_MARKET_FLOW", "OPPOSING_FORCE_REACTION"}
        and execution_side == side
        and bool(payload.get("actionable", False))
    )
    if trade_mode not in {"TREND_FOLLOW", "STAND_ASIDE"} and not pullback_reload_ready and not location_sniper_ready and not market_flow_ready:
        return False, f"kernel trade_mode is {trade_mode or 'MISSING'}"
    major_side = _normalize_trade_side(kernel.get("major_trend_side"))
    dominant_side = _normalize_trade_side(kernel.get("dominant_side"))
    if not (location_sniper_ready or market_flow_ready) and major_side != side:
        return False, f"major trend side {major_side or 'MISSING'} does not match {side}"
    if not (location_sniper_ready or market_flow_ready) and dominant_side != side:
        return False, f"dominant side {dominant_side or 'MISSING'} does not match {side}"
    try:
        target_horizon = int(kernel["target_horizon_candles"])
    except (KeyError, TypeError, ValueError):
        return False, "missing target_horizon_candles"
    if not (location_sniper_ready or market_flow_ready) and target_horizon < STRICT_SWING_MIN_TARGET_CANDLES:
        return False, f"target horizon {target_horizon} candle(s) is below swing minimum"
    if bool(kernel.get("news_event_candidate", False)):
        return False, "kernel marked this as a news-event candidate"
    timeframe_seconds = _strict_signal_timeframe_seconds(
        payload.get("focus_timeframe") or payload.get("timeframe") or payload.get("active_timeframe")
    )
    if timeframe_seconds is None:
        return False, "missing supported explicit timeframe"
    timing_any = payload.get("execution_timing")
    timing_profile = cast(Dict[str, Any], timing_any) if isinstance(timing_any, dict) else {}
    if timing_profile and timing_profile.get("entry_allowed") is False and not _timing_profile_current_flow_ready(timing_profile, side):
        return False, str(timing_profile.get("block_reason") or "PhoenixGuard timing profile rejected this entry")
    if location_sniper_ready:
        significant_entry = bool(timing_profile.get("significant_entry_context", False))
        try:
            entry_area_score = float(timing_profile.get("entry_area_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            entry_area_score = 0.0
        if not significant_entry or entry_area_score < ENTRY_LOCATION_SIGNIFICANT_ENTRY_MIN_SCORE:
            return False, "location sniper requires a significant support/resistance entry area"
    recommended_seconds_raw = timing_profile.get("recommended_expiry_seconds", timing_profile.get("expiry_seconds"))
    recommended_seconds = _parse_expiry_seconds_value(recommended_seconds_raw)
    if recommended_seconds and int(expiry_seconds or 0) > max(int(recommended_seconds * 2.0), int(recommended_seconds + timeframe_seconds)):
        return False, f"expiry {expiry_seconds}s is materially longer than PhoenixGuard timing profile {recommended_seconds}s"
    p_target_before_invalidation = _coerce_nonnegative_seconds(kernel.get("p_target_before_invalidation"))
    if p_target_before_invalidation is None or float(p_target_before_invalidation) < 0.46:
        return False, "target-before-invalidation probability is too weak or missing"
    if location_sniper_ready:
        return True, "strict location sniper gate accepted"
    if market_flow_ready:
        return True, f"strict {execution_lane.lower()} gate accepted"
    if pullback_reload_ready:
        return True, "strict pullback reload gate accepted"
    return True, "strict swing gate accepted"


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


def fetch_tracker_session_snapshot(
    base_url: str,
    session_id: str,
    timeout: float = DEFAULT_LIVE_API_FETCH_TIMEOUT_SECONDS,
) -> Optional[Dict[str, Any]]:
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


def _model_council_wait_summary(tracker_snapshot: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(tracker_snapshot, Mapping):
        return "tracker snapshot unavailable"
    result = dict(tracker_snapshot.get("model_council_result") or {})
    council = dict(result.get("model_council") or tracker_snapshot.get("model_council") or {})
    execution = dict(result.get("execution") or {})
    promotion = dict(result.get("promotion_trace") or council.get("promotion_trace") or {})
    broker_state = dict(tracker_snapshot.get("broker_execution_state") or {})
    state = str(
        council.get("final_state")
        or execution.get("state")
        or broker_state.get("status")
        or tracker_snapshot.get("status")
        or "UNKNOWN"
    ).strip()
    side = str(council.get("final_side") or broker_state.get("side") or "HOLD").strip()
    block_reason = str(
        result.get("block_reason")
        or promotion.get("true_blocker")
        or promotion.get("blocked_by")
        or council.get("arbitration_reason")
        or broker_state.get("message")
        or "no executable packet is published yet"
    ).strip()
    if promotion.get("next_required"):
        block_reason = f"{block_reason}; next={promotion.get('next_required')}"
    if len(block_reason) > 140:
        block_reason = block_reason[:137].rstrip() + "..."
    return f"state={state or 'UNKNOWN'} side={side or 'HOLD'} reason={block_reason}"


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
        f"/v1/mobile/model-council/sessions/{session_q}/execution/latest",
        f"/v1/mobile/model-council/execution/latest?session_id={session_q}",
        f"/v1/mobile/model-council/health?session_id={session_q}",
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
    def __init__(self, session_id: str, base_url: str = "") -> None:
        self._session_id = session_id
        self._base_url = str(base_url or "").rstrip("/")
        status_box_env = str(os.getenv("PHOENIXGUARD_SHOOTER_STATUS_BOX", "1") or "1").strip().lower()
        self._enabled = has_tkinter and tk is not None and ttk is not None and status_box_env not in {"0", "false", "no", "off"}
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._signal_payload: Optional[Dict[str, Any]] = None
        self._tracker_payload: Optional[Dict[str, Any]] = None
        self._action_payload: Optional[Dict[str, Any]] = None
        self._preview_bytes: Optional[bytes] = None
        self._preview_source = ""
        self._preview_hash = ""
        self._preview_rendered_hash = ""
        self._thread: Optional[threading.Thread] = None
        self._preview_thread: Optional[threading.Thread] = None
        self._root: Any = None
        self._preview_label: Any = None
        self._preview_status_var: Any = None
        self._preview_photo: Any = None
        self._signal_var: Any = None
        self._tracker_var: Any = None
        self._age_var: Any = None
        self._updated_var: Any = None
        self._raw_expiry_var: Any = None
        self._raw_side_var: Any = None
        self._action_var: Any = None
        self._cooldown_var: Any = None
        self._cooldown_remaining_seconds = 0
        self._settings_path = _SHOOTER_RUNTIME_DIR / "floating_window_v2.json"
        self._display_mode = "compact"
        self._opacity = 0.94
        self._scale = 1.0
        self._position = (24, 86)
        self._last_render_signature = ""
        self._last_render_second = 0
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._hud_vars: dict[str, Any] = {}
        self._hud_frames: dict[str, Any] = {}

    def start(self) -> None:
        if not self._enabled:
            LOGGER.warning("Status box unavailable because tkinter could not be loaded.")
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="808-shooter-status-box", daemon=True)
        self._thread.start()
        if self._base_url and has_pillow:
            self._preview_thread = threading.Thread(target=self._preview_loop, name="808-shooter-phoenix-preview", daemon=True)
            self._preview_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        root = self._root
        if root is not None:
            try:
                root.after(0, root.destroy)
            except Exception:
                pass

    def _preview_artifact_url(self, kind: str) -> str:
        session_q = urllib.parse.quote(self._session_id)
        return f"{self._base_url}/v1/mobile/window-tracker/sessions/{session_q}/artifacts/latest-{kind}"

    def _preview_loop(self) -> None:
        while not self._stop_event.is_set():
            loaded = False
            for kind in ("overlay", "full-overlay", "chart", "window"):
                if self._stop_event.is_set():
                    return
                try:
                    req = urllib.request.Request(
                        url=self._preview_artifact_url(kind),
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
                        method="GET",
                    )
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        raw = resp.read()
                    if not raw:
                        continue
                    digest = hashlib.sha1(raw).hexdigest()
                    with self._preview_lock:
                        if digest != self._preview_hash:
                            self._preview_bytes = raw
                            self._preview_hash = digest
                            self._preview_source = kind
                    loaded = True
                    break
                except urllib.error.HTTPError as exc:
                    if getattr(exc, "code", None) not in {404, 405}:
                        LOGGER.debug("Shooter preview fetch failed for %s: %s", kind, exc)
                except Exception as exc:
                    if "timed out" not in str(exc).lower():
                        LOGGER.debug("Shooter preview fetch failed for %s: %s", kind, exc)
            time.sleep(DEFAULT_FLOATING_PREVIEW_INTERVAL_SECONDS if loaded else 2.5)

    def _render_preview_image(self) -> None:
        if not (has_pillow and Image is not None and ImageTk is not None and self._preview_label is not None):
            return
        with self._preview_lock:
            raw = self._preview_bytes
            digest = self._preview_hash
            source = self._preview_source
        if not raw or not digest or digest == self._preview_rendered_hash:
            return
        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image.thumbnail((420, 210))
            photo = ImageTk.PhotoImage(image)  # type: ignore[operator]
            self._preview_photo = photo
            self._preview_label.configure(image=photo)
            self._preview_rendered_hash = digest
            if self._preview_status_var is not None:
                self._preview_status_var.set(f"Phoenix view: {source} | realtime mirror")
        except Exception as exc:
            if self._preview_status_var is not None:
                self._preview_status_var.set(f"Phoenix view: unavailable ({exc})")

    def update(self, signal_payload: Optional[Dict[str, Any]], tracker_payload: Optional[Dict[str, Any]], cooldown_remaining_seconds: int = 0) -> None:
        with self._state_lock:
            self._signal_payload = dict(signal_payload) if isinstance(signal_payload, dict) else None
            self._tracker_payload = dict(tracker_payload) if isinstance(tracker_payload, dict) else None
            self._cooldown_remaining_seconds = max(0, int(cooldown_remaining_seconds))

    def update_action(self, action_payload: Optional[Mapping[str, Any]]) -> None:
        with self._state_lock:
            self._action_payload = dict(action_payload) if isinstance(action_payload, Mapping) else None

    def _build_action_text(self, action_payload: Optional[Dict[str, Any]]) -> str:
        if not action_payload:
            return "Shooter Action: waiting for executable packet"
        phase = _truncate_text(action_payload.get("phase") or action_payload.get("state") or "UNKNOWN", 28)
        step = _truncate_text(action_payload.get("step") or action_payload.get("target") or "n/a", 28)
        packet_id = _truncate_text(action_payload.get("packet_id") or "", 18) or "packet"
        side = _normalize_trade_side(action_payload.get("side")) or _truncate_text(action_payload.get("side") or "n/a", 8)
        expiry = action_payload.get("expiry_seconds") or action_payload.get("expiry") or "n/a"
        result = _truncate_text(action_payload.get("result") or action_payload.get("overall") or "", 18)
        suffix = f" | {result}" if result else ""
        return f"Shooter Action: {phase} | {step} | {side} {expiry}s | {packet_id}{suffix}"

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

        execution_packet = signal_payload if isinstance(signal_payload, dict) and str(signal_payload.get("schema_version") or "") == PG_EXECUTION_PACKET_SCHEMA_V3 else {}
        study_packet = resolved_payload.get("model_council_study_packet")
        if not isinstance(study_packet, dict):
            study_packet = (resolved_payload.get("model_council_result") or {}).get("study_packet") if isinstance(resolved_payload.get("model_council_result"), dict) else {}
        if isinstance(execution_packet, dict) and execution_packet:
            execution = execution_packet.get("execution") if isinstance(execution_packet.get("execution"), dict) else {}
            promotion = execution_packet.get("promotion_trace") if isinstance(execution_packet.get("promotion_trace"), dict) else {}
            lane_payload = execution_packet.get("execution_lane")
            lane = ""
            if isinstance(lane_payload, dict):
                lane = str(lane_payload.get("name") or "")
            lane = lane or str(execution_packet.get("selected_execution_lane") or promotion.get("selected_lane") or "")
            packet_id = _truncate_text(execution_packet.get("packet_id", ""), 18) or "missing"
            side = _normalize_trade_side(execution.get("side")) or "HOLD"
            expiry = execution.get("expiry_seconds", "missing")
            lane_text = f" | lane {_truncate_text(lane, 22)}" if lane else ""
            return f"Packet: EXECUTABLE | {side} | expiry {expiry}s{lane_text} | {packet_id}"
        if isinstance(study_packet, dict) and study_packet:
            execution = study_packet.get("execution") if isinstance(study_packet.get("execution"), dict) else {}
            council = study_packet.get("model_council") if isinstance(study_packet.get("model_council"), dict) else {}
            promotion = study_packet.get("promotion_trace") if isinstance(study_packet.get("promotion_trace"), dict) else {}
            lane_payload = study_packet.get("execution_lane") or council.get("execution_lane") or promotion.get("execution_lane")
            lane = ""
            lane_accepted = promotion.get("lane_accepted")
            if isinstance(lane_payload, dict):
                lane = str(lane_payload.get("name") or "")
                if lane_accepted is None:
                    lane_accepted = lane_payload.get("accepted")
            lane = lane or str(study_packet.get("selected_execution_lane") or council.get("selected_execution_lane") or promotion.get("selected_lane") or "")
            packet_id = _truncate_text(study_packet.get("packet_id", ""), 18) or "study"
            state = _truncate_text(execution.get("state") or council.get("final_state") or "WATCHING", 14)
            side = _normalize_trade_side(execution.get("side") or council.get("final_side") or promotion.get("candidate_side")) or "HOLD"
            score = council.get("final_execution_score") or study_packet.get("final_execution_score") or promotion.get("final_execution_score")
            next_required = _truncate_text(promotion.get("next_required") or study_packet.get("block_reason") or "waiting for council promotion", 34)
            score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else "score pending"
            lane_state = "YES" if lane_accepted is True else "NO" if lane_accepted is False else "?"
            lane_text = f" | lane {_truncate_text(lane, 22)}:{lane_state}" if lane else ""
            return f"Packet: STUDY | {state} | {side}{lane_text} | score {score_text} | {next_required} | {packet_id}"

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

    def _load_floating_settings(self) -> None:
        try:
            parsed = json.loads(self._settings_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                mode = str(parsed.get("mode") or self._display_mode).lower()
                if mode in {"mini", "compact", "expanded", "inspector"}:
                    self._display_mode = mode
                self._opacity = min(1.0, max(0.45, float(parsed.get("opacity", self._opacity))))
                self._scale = min(1.45, max(0.75, float(parsed.get("scale", self._scale))))
                x = int(parsed.get("x", self._position[0]))
                y = int(parsed.get("y", self._position[1]))
                if x <= 2:
                    x = 24
                if y <= 2:
                    y = 86
                self._position = (max(0, x), max(0, y))
        except Exception:
            pass

    def _save_floating_settings(self) -> None:
        try:
            root = self._root
            if root is not None:
                self._position = (int(root.winfo_x()), int(root.winfo_y()))
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(
                json.dumps(
                    {
                        "mode": self._display_mode,
                        "opacity": self._opacity,
                        "scale": self._scale,
                        "x": self._position[0],
                        "y": self._position[1],
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _floating_state(self, signal_payload: Optional[Dict[str, Any]], tracker_payload: Optional[Dict[str, Any]], action_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return build_floating_state(
            session_id=self._session_id,
            mode="LIVE",
            signal_payload=signal_payload,
            tracker_payload=tracker_payload,
            action_payload=action_payload,
            cooldown_remaining_seconds=self._cooldown_remaining_seconds,
        )

    def _chip_color(self, chip: str) -> str:
        return {
            "STUDY": "#3B82F6",
            "WAITING": "#64748B",
            "PREPARING": "#F59E0B",
            "EXECUTABLE": "#22C55E",
            "ACTION": "#06B6D4",
            "COOLDOWN": "#F59E0B",
            "BLOCKED": "#EF4444",
        }.get(str(chip or "").upper(), "#64748B")

    def _side_color(self, side: str) -> str:
        return "#2FCE65" if side == "BUY" else "#FF4B42" if side == "SELL" else "#CBD5E1"

    def _score_text(self, state: Mapping[str, Any]) -> str:
        council = state.get("council") if isinstance(state.get("council"), Mapping) else {}
        score = council.get("final_score")
        threshold = council.get("threshold")
        if isinstance(score, (int, float)) and isinstance(threshold, (int, float)):
            return f"{float(score):.2f} / {float(threshold):.2f}"
        return "Score pending"

    def _score_bar(self, state: Mapping[str, Any], cells: int = 12) -> str:
        council = state.get("council") if isinstance(state.get("council"), Mapping) else {}
        score = council.get("final_score")
        threshold = council.get("threshold") or 0.70
        if not isinstance(score, (int, float)):
            return "[" + ("-" * cells) + "]"
        ratio = max(0.0, min(1.0, float(score) / max(0.01, float(threshold))))
        filled = int(round(ratio * cells))
        return "[" + ("█" * filled) + ("░" * (cells - filled)) + "]"

    def _gap_text(self, state: Mapping[str, Any]) -> str:
        council = state.get("council") if isinstance(state.get("council"), Mapping) else {}
        gap = council.get("score_gap")
        if isinstance(gap, (int, float)):
            if float(gap) <= 0:
                return "Score passed"
            return f"Need +{float(gap):.2f}"
        return "Waiting for score"

    def _score_grid_text(self, state: Mapping[str, Any]) -> str:
        scores = state.get("scores") if isinstance(state.get("scores"), Mapping) else {}

        def fmt(key: str) -> str:
            value = scores.get(key)
            return f"{float(value):.2f}" if isinstance(value, (int, float)) else "--"

        return f"G {fmt('global')}   L {fmt('local')}   Z {fmt('zone')}\nA {fmt('angle')}   H {fmt('history')}   R {fmt('risk')}"

    def _health_text(self, state: Mapping[str, Any]) -> str:
        health = state.get("health") if isinstance(state.get("health"), Mapping) else {}
        awake = health.get("models_awake")
        total = health.get("models_total")
        models = f"{awake}/{total}" if isinstance(awake, int) and isinstance(total, int) and total else "models"
        age = health.get("latency_sec")
        age_text = f"{float(age):.1f}s" if isinstance(age, (int, float)) else "sync"
        cache = str(health.get("cache") or "FRESH").title()
        tracker = str(health.get("tracker") or "WAITING").title()
        return f"{tracker} | Models {models} | Packet {age_text} | {cache}"

    def _cooldown_display(self, state: Mapping[str, Any]) -> str:
        shooter = state.get("shooter") if isinstance(state.get("shooter"), Mapping) else {}
        remaining = shooter.get("cooldown_remaining_sec")
        if not isinstance(remaining, int) or remaining <= 0:
            return "Cooldown ready"
        minutes, seconds = divmod(remaining, 60)
        return f"Cooldown {minutes}:{seconds:02d}"

    def _set_var(self, key: str, value: Any) -> None:
        var = self._hud_vars.get(key)
        if var is not None:
            var.set(str(value))

    def _mode_geometry(self) -> tuple[int, int]:
        base = {
            "mini": (230, 86),
            "compact": (340, 260),
            "expanded": (460, 520),
            "inspector": (520, 560),
        }.get(self._display_mode, (340, 260))
        return int(base[0] * self._scale), int(base[1] * self._scale)

    def _hud_font_size(self, size: int) -> int:
        return max(6, int(size * self._scale))

    def _set_display_mode(self, mode: str) -> None:
        if mode not in {"mini", "compact", "expanded", "inspector"}:
            return
        self._display_mode = mode
        self._last_render_signature = ""
        root = self._root
        if root is not None:
            self._apply_mode_geometry()
        self._save_floating_settings()

    def _apply_mode_geometry(self) -> None:
        root = self._root
        if root is None:
            return
        width, height = self._mode_geometry()
        try:
            root.geometry(f"{width}x{height}+{root.winfo_x()}+{root.winfo_y()}")
        except Exception:
            root.geometry(f"{width}x{height}+{self._position[0]}+{self._position[1]}")
        for name, frame in self._hud_frames.items():
            try:
                frame.pack_forget()
            except Exception:
                pass
        frame = self._hud_frames.get(self._display_mode)
        if frame is not None:
            frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _snap(self, corner: str) -> None:
        root = self._root
        if root is None:
            return
        width, height = self._mode_geometry()
        screen_w = int(root.winfo_screenwidth())
        screen_h = int(root.winfo_screenheight())
        margin = 14
        x = screen_w - width - margin
        y = margin if corner == "top_right" else screen_h - height - margin
        root.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self._position = (max(0, x), max(0, y))
        self._save_floating_settings()

    def _render_floating_state(self, state: Mapping[str, Any]) -> None:
        packet = state.get("packet") if isinstance(state.get("packet"), Mapping) else {}
        council = state.get("council") if isinstance(state.get("council"), Mapping) else {}
        timing = state.get("timing") if isinstance(state.get("timing"), Mapping) else {}
        shooter = state.get("shooter") if isinstance(state.get("shooter"), Mapping) else {}
        chip = str(state.get("state_chip") or packet.get("type") or "WAITING").upper()
        side = str(council.get("side") or packet.get("side") or "")
        lane = str(council.get("lane_short") or council.get("lane") or "LANE PENDING")
        lane_status = str(council.get("lane_status") or "WAITING").title()
        reason = str(council.get("reason_short") or council.get("next_required") or "Waiting for packet")
        action = str(shooter.get("action") or "Waiting for executable packet")
        timing_summary = str(timing.get("summary") or "").strip()
        if timing_summary:
            action = timing_summary

        self._set_var("chip", chip)
        self._set_var("session", str(state.get("session_id") or self._session_id))
        self._set_var("mode", f"● {str(state.get('mode') or 'LIVE')}")
        self._set_var("clock", time.strftime("%H:%M:%S"))
        self._set_var("packet_line", f"{packet.get('type', 'WAITING')}    {council.get('state', 'WAITING')}")
        self._set_var("side_score", f"{side or 'No side yet'}    {self._score_text(state)}")
        self._set_var("score_bar", f"{self._score_bar(state)}  {self._gap_text(state)}")
        self._set_var("lane", lane)
        self._set_var("lane_status", f"{lane_status}: {reason}")
        self._set_var("action", action)
        self._set_var("health", self._health_text(state))
        self._set_var("cooldown", self._cooldown_display(state))
        self._set_var("scores", self._score_grid_text(state))
        self._set_var("mini", f"{side or 'WAIT'} | {packet.get('type', 'WAITING')} | {self._score_text(state)}")
        self._set_var("mini_action", action)

        packet_id = packet.get("id_short")
        inspector = state.get("inspector") if isinstance(state.get("inspector"), Mapping) else {}
        inspector_text = json.dumps(
            {
                "packet": packet,
                "council": council,
                "scores": state.get("scores"),
                "shooter": shooter,
                "health": state.get("health"),
                "packet_id_short": packet_id,
                "reason": reason,
                "raw": inspector,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
        text_widget = self._hud_vars.get("inspector_text")
        if text_widget is not None:
            try:
                text_widget.configure(state="normal")
                text_widget.delete("1.0", "end")
                text_widget.insert("1.0", inspector_text)
                text_widget.configure(state="disabled")
            except Exception:
                pass

        chip_label = self._hud_vars.get("chip_label")
        if chip_label is not None:
            try:
                chip_label.configure(bg=self._chip_color(chip), fg="#F8FAFC")
            except Exception:
                pass
        side_labels = self._hud_vars.get("side_labels")
        if isinstance(side_labels, list):
            for label in side_labels:
                try:
                    label.configure(fg=self._side_color(side))
                except Exception:
                    pass

    def _run(self) -> None:
        self._load_floating_settings()
        root = tk.Tk()  # type: ignore[call-arg]
        self._root = root
        root.title("PhoenixGuard HUD")
        width, height = self._mode_geometry()
        root.geometry(f"{width}x{height}+{self._position[0]}+{self._position[1]}")
        root.resizable(True, True)
        root_any = cast(Any, root)
        root_any.attributes("-topmost", True)  # type: ignore[reportUnknownMemberType]
        root_any.attributes("-alpha", self._opacity)  # type: ignore[reportUnknownMemberType]
        try:
            root_any.attributes("-toolwindow", True)  # type: ignore[reportUnknownMemberType]
        except Exception:
            pass
        try:
            root.overrideredirect(True)
        except Exception:
            pass
        root.after(50, lambda: root.geometry(f"{width}x{height}+{self._position[0]}+{self._position[1]}"))
        root.configure(bg="#07111F")
        root.bind("<Unmap>", lambda _event: root.after_idle(root.deiconify))
        root.protocol("WM_DELETE_WINDOW", lambda: None)

        bg = "#07111F"
        panel = "#0B1626"
        card = "#101C2E"
        line = "#26364E"
        text = "#E5EDF7"
        muted = "#8EA0B8"

        def make_var(name: str, value: str = "") -> Any:
            var = tk.StringVar(value=value)  # type: ignore[attr-defined]
            self._hud_vars[name] = var
            return var

        for name in ("chip", "session", "mode", "clock", "packet_line", "side_score", "score_bar", "lane", "lane_status", "action", "health", "cooldown", "scores", "mini", "mini_action"):
            make_var(name)

        def label(parent: Any, key: str, *, size: int = 9, weight: str = "normal", fg: str = text, bg_color: str = panel, anchor: str = "w", pady: tuple[int, int] = (0, 0)) -> Any:
            lbl = tk.Label(parent, textvariable=self._hud_vars[key], bg=bg_color, fg=fg, anchor=anchor, justify="left", padx=8, font=("Segoe UI", self._hud_font_size(size), weight))  # type: ignore[attr-defined]
            lbl.pack(fill="x", pady=pady)
            return lbl

        def static_label(parent: Any, value: str, *, size: int = 8, weight: str = "normal", fg: str = muted, bg_color: str = panel) -> Any:
            lbl = tk.Label(parent, text=value, bg=bg_color, fg=fg, anchor="w", padx=8, font=("Segoe UI", self._hud_font_size(size), weight))  # type: ignore[attr-defined]
            lbl.pack(fill="x")
            return lbl

        header = tk.Frame(root, bg=bg)  # type: ignore[attr-defined]
        header.pack(fill="x", padx=8, pady=(7, 5))
        title_col = tk.Frame(header, bg=bg)  # type: ignore[attr-defined]
        title_col.pack(side="left", fill="x", expand=True)
        title_lbl = tk.Label(title_col, text="PhoenixGuard", bg=bg, fg="#F8FAFC", anchor="w", font=("Segoe UI", self._hud_font_size(9), "bold"))  # type: ignore[attr-defined]
        title_lbl.pack(fill="x")
        session_row = tk.Frame(title_col, bg=bg)  # type: ignore[attr-defined]
        session_row.pack(fill="x")
        session_lbl = tk.Label(session_row, textvariable=self._hud_vars["session"], bg=bg, fg=muted, anchor="w", font=("Segoe UI", self._hud_font_size(7)))  # type: ignore[attr-defined]
        session_lbl.pack(side="left")
        clock_lbl = tk.Label(session_row, textvariable=self._hud_vars["clock"], bg=bg, fg="#A7F3D0", anchor="w", padx=8, font=("Segoe UI", self._hud_font_size(7), "bold"))  # type: ignore[attr-defined]
        clock_lbl.pack(side="left")

        control_fg = "#C7D2FE"
        control_bg = "#111827"
        expand_btn = tk.Button(header, text="+", command=lambda: self._set_display_mode("expanded" if self._display_mode != "expanded" else "compact"), bg=control_bg, fg=control_fg, activebackground="#1F2937", activeforeground="#F8FAFC", relief="flat", padx=5, pady=1, font=("Segoe UI", self._hud_font_size(7), "bold"))  # type: ignore[attr-defined]
        expand_btn.pack(side="right", padx=(2, 0))
        mini_btn = tk.Button(header, text="-", command=lambda: self._set_display_mode("mini"), bg=control_bg, fg=control_fg, activebackground="#1F2937", activeforeground="#F8FAFC", relief="flat", padx=6, pady=1, font=("Segoe UI", self._hud_font_size(7), "bold"))  # type: ignore[attr-defined]
        mini_btn.pack(side="right", padx=(4, 0))
        chip_lbl = tk.Label(header, textvariable=self._hud_vars["chip"], bg="#3B82F6", fg="#F8FAFC", padx=8, pady=3, font=("Segoe UI", self._hud_font_size(7), "bold"))  # type: ignore[attr-defined]
        chip_lbl.pack(side="right", padx=(6, 0))
        self._hud_vars["chip_label"] = chip_lbl
        live_lbl = tk.Label(header, textvariable=self._hud_vars["mode"], bg=bg, fg="#34D399", padx=4, pady=3, font=("Segoe UI", self._hud_font_size(7), "bold"))  # type: ignore[attr-defined]
        live_lbl.pack(side="right")

        side_labels: list[Any] = []

        def hud_frame(name: str) -> Any:
            frame = tk.Frame(root, bg=panel, highlightbackground=line, highlightthickness=1)  # type: ignore[attr-defined]
            self._hud_frames[name] = frame
            return frame

        mini = hud_frame("mini")
        label(mini, "mini", size=8, weight="bold", bg_color=panel, pady=(6, 2))
        label(mini, "mini_action", size=7, fg=muted, bg_color=panel, pady=(0, 5))

        compact = hud_frame("compact")
        label(compact, "packet_line", size=8, weight="bold", bg_color=panel, pady=(7, 1))
        side_labels.append(label(compact, "side_score", size=12, weight="bold", bg_color=panel, pady=(0, 4)))
        label(compact, "score_bar", size=7, fg="#B6C5DC", bg_color=panel, pady=(0, 5))
        static_label(compact, "Lane", size=7, bg_color=panel)
        label(compact, "lane", size=10, weight="bold", fg="#DDE8FF", bg_color=panel)
        label(compact, "lane_status", size=7, fg=muted, bg_color=panel, pady=(0, 6))
        label(compact, "action", size=8, weight="bold", fg="#EAF2FF", bg_color=panel, pady=(0, 4))
        label(compact, "health", size=6, fg=muted, bg_color=panel, pady=(0, 5))

        expanded = hud_frame("expanded")
        exp_packet = tk.Frame(expanded, bg=card, padx=10, pady=8)  # type: ignore[attr-defined]
        exp_packet.pack(fill="x", padx=8, pady=(8, 6))
        label(exp_packet, "packet_line", size=10, weight="bold", bg_color=card)
        side_labels.append(label(exp_packet, "side_score", size=15, weight="bold", bg_color=card))
        label(exp_packet, "score_bar", size=9, fg="#B6C5DC", bg_color=card)

        exp_lane = tk.Frame(expanded, bg=card, padx=10, pady=8)  # type: ignore[attr-defined]
        exp_lane.pack(fill="x", padx=8, pady=6)
        static_label(exp_lane, "Execution Lane", bg_color=card)
        label(exp_lane, "lane", size=12, weight="bold", fg="#DDE8FF", bg_color=card)
        label(exp_lane, "lane_status", size=9, fg=muted, bg_color=card)

        exp_scores = tk.Frame(expanded, bg=card, padx=10, pady=8)  # type: ignore[attr-defined]
        exp_scores.pack(fill="x", padx=8, pady=6)
        static_label(exp_scores, "Model Council Scores", bg_color=card)
        label(exp_scores, "scores", size=10, fg="#D1FAE5", bg_color=card)

        exp_action = tk.Frame(expanded, bg=card, padx=10, pady=8)  # type: ignore[attr-defined]
        exp_action.pack(fill="x", padx=8, pady=6)
        static_label(exp_action, "Shooter Action", bg_color=card)
        label(exp_action, "action", size=10, weight="bold", bg_color=card)
        label(exp_action, "cooldown", size=8, fg="#FBBF24", bg_color=card)
        label(exp_action, "health", size=8, fg=muted, bg_color=card)

        preview_card = tk.Frame(expanded, bg=card, padx=8, pady=8)  # type: ignore[attr-defined]
        preview_card.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        self._preview_status_var = tk.StringVar(value="Phoenix view: realtime mirror")  # type: ignore[attr-defined]
        self._preview_label = tk.Label(preview_card, text="Phoenix surface preview", bg="#050A12", fg=muted, height=7, anchor="center", font=("Segoe UI", 8))  # type: ignore[attr-defined]
        self._preview_label.pack(fill="both", expand=True)

        inspector = hud_frame("inspector")
        inspector_text = tk.Text(inspector, bg="#050A12", fg="#C7D2FE", insertbackground="#C7D2FE", relief="flat", wrap="word", font=("Consolas", max(7, int(8 * self._scale))))  # type: ignore[attr-defined]
        inspector_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scrollbar = tk.Scrollbar(inspector, command=inspector_text.yview)  # type: ignore[attr-defined]
        scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=8)
        inspector_text.configure(yscrollcommand=scrollbar.set, state="disabled")
        self._hud_vars["inspector_text"] = inspector_text
        self._hud_vars["side_labels"] = side_labels

        self._apply_mode_geometry()

        def _on_press(evt: Any) -> None:
            try:
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

        def _on_release(_evt: Any) -> None:
            self._save_floating_settings()

        def _toggle(_evt: Any = None) -> None:
            self._set_display_mode("compact" if self._display_mode == "mini" else "mini")

        def _wheel(evt: Any) -> None:
            try:
                if int(evt.state) & 0x0004:
                    self._scale = min(1.45, max(0.75, self._scale + (0.05 if int(evt.delta) > 0 else -0.05)))
                    self._apply_mode_geometry()
                    self._save_floating_settings()
            except Exception:
                pass

        menu = tk.Menu(root, tearoff=False, bg="#0B1626", fg="#E5EDF7")  # type: ignore[attr-defined]
        for mode_name in ("mini", "compact", "expanded", "inspector"):
            menu.add_command(label=f"{mode_name.title()} Mode", command=lambda m=mode_name: self._set_display_mode(m))
        menu.add_separator()
        menu.add_command(label="Opacity +", command=lambda: (setattr(self, "_opacity", min(1.0, self._opacity + 0.05)), root_any.attributes("-alpha", self._opacity), self._save_floating_settings()))
        menu.add_command(label="Opacity -", command=lambda: (setattr(self, "_opacity", max(0.45, self._opacity - 0.05)), root_any.attributes("-alpha", self._opacity), self._save_floating_settings()))
        menu.add_command(label="Scale +", command=lambda: (setattr(self, "_scale", min(1.45, self._scale + 0.05)), self._apply_mode_geometry(), self._save_floating_settings()))
        menu.add_command(label="Scale -", command=lambda: (setattr(self, "_scale", max(0.75, self._scale - 0.05)), self._apply_mode_geometry(), self._save_floating_settings()))
        menu.add_separator()
        menu.add_command(label="Snap Top Right", command=lambda: self._snap("top_right"))
        menu.add_command(label="Snap Bottom Right", command=lambda: self._snap("bottom_right"))
        menu.add_command(label="Reset Position", command=lambda: (root.geometry(f"{self._mode_geometry()[0]}x{self._mode_geometry()[1]}+20+20"), setattr(self, "_position", (20, 20)), self._save_floating_settings()))

        def _show_menu(evt: Any) -> None:
            try:
                menu.tk_popup(int(evt.x_root), int(evt.y_root))
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass

        widgets = [root, header, title_col, session_row, title_lbl, session_lbl, clock_lbl, chip_lbl, live_lbl, mini, compact, expanded, inspector]

        def _append_children(widget: Any) -> None:
            try:
                for child in widget.winfo_children():
                    if child not in widgets:
                        widgets.append(child)
                        _append_children(child)
            except Exception:
                pass

        for frame in self._hud_frames.values():
            _append_children(frame)

        for widget in widgets:
            try:
                widget.bind("<ButtonPress-1>", _on_press)
                widget.bind("<B1-Motion>", _on_motion)
                widget.bind("<ButtonRelease-1>", _on_release)
                widget.bind("<Double-Button-1>", _toggle)
                widget.bind("<Button-3>", _show_menu)
                widget.bind("<Control-MouseWheel>", _wheel)
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
                action_payload = dict(self._action_payload) if self._action_payload is not None else None

            floating_state = self._floating_state(signal_payload, tracker_payload, action_payload)
            packet_for_signature = dict(floating_state.get("packet") or {})
            health_for_signature = dict(floating_state.get("health") or {})
            for bucket in (packet_for_signature, health_for_signature):
                latency_value = bucket.get("age_sec") if "age_sec" in bucket else bucket.get("latency_sec")
                if isinstance(latency_value, (int, float)):
                    if "age_sec" in bucket:
                        bucket["age_sec"] = int(float(latency_value))
                    if "latency_sec" in bucket:
                        bucket["latency_sec"] = int(float(latency_value))
            signature = json.dumps(
                {
                    "mode": self._display_mode,
                    "chip": floating_state.get("state_chip"),
                    "packet": packet_for_signature,
                    "council": floating_state.get("council"),
                    "scores": floating_state.get("scores"),
                    "shooter": floating_state.get("shooter"),
                    "health": health_for_signature,
                },
                sort_keys=True,
                default=str,
            )
            now_second = int(time.time())
            if signature != self._last_render_signature or now_second != self._last_render_second:
                self._last_render_signature = signature
                self._last_render_second = now_second
                self._render_floating_state(floating_state)
                if self._display_mode == "expanded":
                    self._render_preview_image()

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
    raw_value = payload.get("execution_action")
    side = _normalize_trade_side(raw_value)
    if _coerce_signal_bool(payload.get("actionable", False)) and side in {"BUY", "SELL"}:
        return side, "authoritative_field(execution_action)", "execution_action", raw_value, "explicit"

    if _kernel_requests_trigger(payload):
        trigger_side, trigger_source = _extract_trigger_side(payload)
        if trigger_side in {"BUY", "SELL"} and _signal_has_trigger_consensus(payload, trigger_side):
            return (
                trigger_side,
                f"trigger_promoted({trigger_source})",
                trigger_source or "trigger_fallback",
                trigger_side,
                "kernel trigger and directional consensus",
            )

    if not _coerce_signal_bool(payload.get("actionable", False)):
        return None, "none", "actionable", payload.get("actionable"), "payload actionable flag is not true"

    if side not in {"BUY", "SELL"}:
        return None, "none", "execution_action", raw_value, "missing explicit BUY/SELL execution_action"

    return None, "none", "execution_action", raw_value, "missing explicit BUY/SELL execution_action"


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


def _log_safe_text(value: Any) -> str:
    return str(value or "").encode("ascii", "backslashreplace").decode("ascii")


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
    preferred_hwnd: Optional[int] = None,
    allow_active_fallback: bool = True,
    quiet: bool = False,
) -> Optional[int]:
    """Find target broker window by explicit query, then Pocket Option/trading heuristics."""
    all_windows = list_visible_windows()
    query_miss = False
    preferred = int(preferred_hwnd or 0)
    if preferred > 0:
        for hwnd, title, class_name in all_windows:
            if int(hwnd) != preferred:
                continue
            if window_query and window_query.lower().strip() not in title.lower():
                break
            if not quiet:
                LOGGER.info(
                    "Window selected by locked HWND %s: class=%s | title=%s",
                    preferred,
                    class_name,
                    _log_safe_text(title),
                )
            return int(hwnd)
        if not quiet:
            LOGGER.warning("Preferred broker HWND %s is not visible or no longer matches query.", preferred)

    if window_query:
        filtered = list_visible_windows(window_query)
        if filtered:
            filtered.sort(key=lambda row: len(row[1]), reverse=True)
            hwnd, title, class_name = filtered[0]
            if not quiet:
                LOGGER.info(
                    "Window selected by query '%s': HWND=%s | class=%s | title=%s",
                    window_query,
                    hwnd,
                    class_name,
                    _log_safe_text(title),
                )
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
                    _log_safe_text(title),
                )
            return hwnd

        foreground = USER32.GetForegroundWindow() if allow_active_fallback and not query_miss else 0
        if foreground:
            fg_title = _window_title(foreground).strip()
            fg_class = _window_class(foreground).strip()
            if fg_title and _is_browser_window(fg_title, fg_class):
                if not quiet:
                    LOGGER.warning(
                        "Using active window fallback: HWND=%s | class=%s | title=%s",
                        foreground,
                        fg_class,
                        _log_safe_text(fg_title),
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
        LOGGER.info("Found Pocket Option window: HWND=%s | class=%s | title=%s", hwnd, class_name, _log_safe_text(title))
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
    preferred_hwnd: Optional[int] = None,
    auto_open: bool = False,
    broker_url: str = DEFAULT_BROKER_URL,
    open_timeout: float = DEFAULT_BROKER_OPEN_TIMEOUT,
    allow_active_fallback: bool = True,
) -> Optional[int]:
    """Find the broker window, optionally opening Pocket Option before failing."""
    find_kwargs: Dict[str, Any] = {
        "allow_active_fallback": allow_active_fallback,
        "quiet": auto_open,
    }
    if preferred_hwnd is not None:
        find_kwargs["preferred_hwnd"] = preferred_hwnd
    hwnd = find_pocket_option_window(window_query, **find_kwargs)
    if hwnd is not None:
        return hwnd

    if not auto_open:
        return None

    if not open_broker_window(broker_url):
        return None

    deadline = time.time() + max(1.0, float(open_timeout))
    while time.time() < deadline:
        retry_kwargs: Dict[str, Any] = {"allow_active_fallback": False, "quiet": True}
        if preferred_hwnd is not None:
            retry_kwargs["preferred_hwnd"] = preferred_hwnd
        hwnd = find_pocket_option_window(window_query, **retry_kwargs)
        if hwnd is not None:
            LOGGER.info("Pocket Option broker window ready after auto-open.")
            return hwnd
        time.sleep(0.50)

    LOGGER.error("Pocket Option did not become visible within %.1fs after auto-open.", float(open_timeout))
    return None


def activate_window(hwnd: int) -> bool:
    """Bring the target window to front without changing its size/state."""
    try:
        hwnd_int = int(hwnd or 0)
        if hwnd_int <= 0:
            return False
        hwnd_value = hwnd_int
        try:
            # Only restore minimized windows. Calling SW_RESTORE on an already
            # maximized browser can resize it back to a restored window.
            if bool(USER32.IsIconic(hwnd_value)):
                USER32.ShowWindow(hwnd_value, 9)  # SW_RESTORE
        except Exception:
            pass
        try:
            allow_set_foreground = getattr(USER32, "AllowSetForegroundWindow", None)
            if allow_set_foreground is not None:
                allow_set_foreground(-1)
        except Exception:
            pass

        attached_threads: list[int] = []
        attach_thread_input = getattr(USER32, "AttachThreadInput", None)
        get_window_thread = getattr(USER32, "GetWindowThreadProcessId", None)
        get_foreground = getattr(USER32, "GetForegroundWindow", None)
        current_thread_id = 0
        target_thread_id = 0
        foreground_thread_id = 0
        try:
            current_thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId() or 0)
        except Exception:
            current_thread_id = 0
        if get_window_thread is not None:
            try:
                target_thread_id = int(get_window_thread(hwnd_value, None) or 0)
            except Exception:
                target_thread_id = 0
            if get_foreground is not None:
                try:
                    foreground_hwnd = get_foreground()
                    if foreground_hwnd:
                        foreground_thread_id = int(get_window_thread(foreground_hwnd, None) or 0)
                except Exception:
                    foreground_thread_id = 0
        if attach_thread_input is not None and current_thread_id > 0:
            for thread_id in {foreground_thread_id, target_thread_id}:
                if thread_id > 0 and thread_id != current_thread_id:
                    try:
                        if bool(attach_thread_input(current_thread_id, thread_id, True)):
                            attached_threads.append(thread_id)
                    except Exception:
                        pass
        foreground_request_ok = False
        try:
            bring_to_top = getattr(USER32, "BringWindowToTop", None)
            if bring_to_top is not None:
                bring_to_top(hwnd_value)
            foreground_request_ok = bool(USER32.SetForegroundWindow(hwnd_value))
            USER32.SetFocus(hwnd_value)
        finally:
            if attach_thread_input is not None and current_thread_id > 0:
                for thread_id in attached_threads:
                    try:
                        attach_thread_input(current_thread_id, thread_id, False)
                    except Exception:
                        pass
        if get_foreground is None:
            return foreground_request_ok
        for _ in range(5):
            time.sleep(0.06)
            if is_window_foreground(hwnd_int):
                return True
        return is_window_foreground(hwnd_int)
    except Exception as exc:
        LOGGER.error("Failed to activate target window: %s", exc)
        return False


def is_window_foreground(hwnd: int) -> bool:
    try:
        return int(USER32.GetForegroundWindow()) == int(hwnd)
    except Exception:
        return False


def ensure_window_foreground(hwnd: int) -> bool:
    if is_window_foreground(hwnd):
        return True
    if not activate_window(hwnd):
        return False
    time.sleep(0.12)
    return is_window_foreground(hwnd)


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


def _rect_matches_cached_expiry(current: RECT, cached_bounds: Any, *, tolerance_px: int = 40) -> bool:
    if not isinstance(cached_bounds, (list, tuple)) or len(cached_bounds) != 4:
        return False
    current_bounds = rect_bounds(current)
    try:
        cached = tuple(int(value) for value in cached_bounds)
    except Exception:
        return False
    return all(abs(int(now) - int(was)) <= int(tolerance_px) for now, was in zip(current_bounds, cached))


def _get_cached_confirmed_expiry(hwnd: int, rect: Optional[RECT], target_seconds: int, *, max_age_sec: float = 900.0) -> Optional[int]:
    if rect is None or int(target_seconds) <= 0:
        return None
    now = time.time()
    with _confirmed_expiry_cache_lock:
        cached = dict(_confirmed_expiry_cache)
    if int(cached.get("hwnd") or 0) != int(hwnd):
        return None
    try:
        cached_seconds = int(cached.get("seconds") or 0)
        cached_epoch = float(cached.get("epoch") or 0.0)
    except Exception:
        return None
    if cached_seconds != int(target_seconds):
        return None
    if cached_epoch <= 0.0 or now - cached_epoch > float(max_age_sec):
        return None
    if not _rect_matches_cached_expiry(rect, cached.get("rect")):
        return None
    return cached_seconds


def _remember_confirmed_expiry(hwnd: int, rect: Optional[RECT], seconds: int, *, source: str = "") -> None:
    if rect is None or int(seconds) <= 0:
        return
    with _confirmed_expiry_cache_lock:
        _confirmed_expiry_cache.clear()
        _confirmed_expiry_cache.update(
            {
                "hwnd": int(hwnd),
                "seconds": int(seconds),
                "epoch": time.time(),
                "rect": list(rect_bounds(rect)),
                "source": str(source or "confirmed"),
            }
        )


_MANIFEST_TARGET_TO_RUNTIME_BOXES: Dict[str, Tuple[str, ...]] = {
    "buy_button": ("buy_button", "buy_icon"),
    "sell_button": ("sell_button", "sell_icon"),
    "expiry_time_field": ("time_button", "time_input", "time_box", "expiry_time_field"),
    "expiry_plus": ("hourly_plus", "expiry_plus"),
    "expiry_minus": ("hourly_minus", "expiry_minus"),
    "hourly_minus": ("hourly_minus", "hour_minus", "hours_minus"),
    "hourly_plus": ("hourly_plus", "hour_plus", "hours_plus"),
    "hourly_input": ("hourly_input", "hour_input", "hours_input"),
    "minute_minus": ("minute_minus", "minutely_minus", "minutes_minus"),
    "minute_plus": ("minute_plus", "minutely_plus", "minutes_plus"),
    "minute_input": ("minute_input", "minutely_input", "minutes_input"),
    "second_minus": ("second_minus", "seconds_minus"),
    "second_plus": ("second_plus", "seconds_plus"),
    "second_input": ("second_input", "seconds_input", "second_field", "seconds_field"),
    "time_3": ("time_3", "time_preset_3"),
    "time_15": ("time_15", "time_preset_15"),
    "time_30": ("time_30", "time_preset_30"),
    "time_60": ("time_60", "time_preset_60"),
    "time_120": ("time_120", "time_preset_120"),
    "time_180": ("time_180", "time_preset_180"),
    "time_300": ("time_300", "time_preset_300"),
    "time_1800": ("time_1800", "time_preset_1800"),
    "time_3600": ("time_3600", "time_preset_3600"),
    "time_14400": ("time_14400", "time_preset_14400"),
    "broker_focus_area": ("broker_screen", "broker_focus_area"),
    "chart_area": ("final_screen",),
    "confirmation_button": ("confirmation_button",),
    "confirmation_area": ("confirmation_area",),
    "position_area": ("position_area",),
    "open_position_area": ("open_position_area",),
}

_MANIFEST_RUNTIME_TARGET_PRIORITY: Dict[str, Tuple[str, ...]] = {
    "buy_button": ("buy_button", "buy_icon"),
    "sell_button": ("sell_button", "sell_icon"),
    "expiry_time_field": ("time_input", "time_button", "time_box", "expiry_time_field"),
    "expiry_plus": ("hourly_plus", "expiry_plus"),
    "expiry_minus": ("hourly_minus", "expiry_minus"),
    "broker_focus_area": ("broker_screen", "broker_focus_area", "final_screen"),
    "chart_area": ("final_screen", "chart_area"),
    "hourly_minus": ("hourly_minus", "hour_minus", "hours_minus", "expiry_minus"),
    "hourly_plus": ("hourly_plus", "hour_plus", "hours_plus", "expiry_plus"),
    "hourly_input": ("hourly_input", "hour_input", "hours_input"),
    "minute_minus": ("minute_minus", "minutely_minus", "minutes_minus", "minute_down"),
    "minute_plus": ("minute_plus", "minutely_plus", "minutes_plus", "minute_up"),
    "minute_input": ("minute_input", "minutely_input", "minutes_input"),
    "second_minus": ("second_minus", "seconds_minus", "second_down"),
    "second_plus": ("second_plus", "seconds_plus", "second_up"),
    "second_input": ("second_input", "seconds_input", "second_field", "seconds_field"),
}

_MANIFEST_SOURCE_TIMING_KEYS: Tuple[str, ...] = (
    "hourly_minus",
    "hourly_plus",
    "hourly_input",
    "minute_minus",
    "minute_plus",
    "minute_input",
    "second_minus",
    "second_plus",
    "second_input",
    "time_3",
    "time_15",
    "time_30",
    "time_60",
    "time_120",
    "time_180",
    "time_300",
    "time_1800",
    "time_3600",
    "time_14400",
)


def _manifest_point(record: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    point = record.get("point")
    if not isinstance(point, Mapping):
        return None
    x = _coerce_finite_float(point.get("x"))
    y = _coerce_finite_float(point.get("y"))
    if x is None or y is None or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    if record.get("marked") is not True:
        return None
    if str(record.get("status") or "").strip().upper() != "USER_CALIBRATED":
        return None
    return {"x": float(x), "y": float(y)}


def _runtime_box_point(record: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    x = _coerce_finite_float(record.get("x"))
    y = _coerce_finite_float(record.get("y"))
    if x is None or y is None or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return {"x": float(x), "y": float(y)}


def _manifest_runtime_point(
    manifest_target: str,
    record: Mapping[str, Any],
    source_boxes: Mapping[str, Mapping[str, Any]],
) -> tuple[Optional[Dict[str, float]], str, str]:
    manifest_point = _manifest_point(record)
    if manifest_point is None:
        return None, "", ""

    source_key = str(record.get("source_key") or "").strip()
    source_candidates: List[str] = []
    if source_key:
        source_candidates.append(source_key)
    source_candidates.extend(_MANIFEST_RUNTIME_TARGET_PRIORITY.get(manifest_target, (manifest_target,)))
    seen: set[str] = set()
    for candidate in source_candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        source_record = source_boxes.get(candidate)
        if not isinstance(source_record, Mapping):
            continue
        source_point = _runtime_box_point(source_record)
        if source_point is not None:
            return source_point, candidate, "user_calibration_manifest_runtime_artifact"
    return manifest_point, source_key, "user_calibration_manifest"


def _manifest_source_boxes(manifest_path: Path, layout: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    candidates: List[Path] = []
    source_path = str(layout.get("source_boxes_path") or "").strip()
    if source_path:
        candidate = Path(source_path)
        candidates.append(candidate if candidate.is_absolute() else manifest_path.parent / candidate)
    runtime_artifacts = layout.get("runtime_artifacts")
    if isinstance(runtime_artifacts, Sequence) and not isinstance(runtime_artifacts, (str, bytes, bytearray)):
        for artifact in runtime_artifacts:
            text = str(artifact or "").strip()
            if not text:
                continue
            candidate = Path(text)
            candidates.append(candidate if candidate.is_absolute() else manifest_path.parent / candidate)
    for candidate in candidates:
        if not candidate.exists() or candidate.suffix.lower() != ".json":
            continue
        try:
            parsed = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(parsed, Mapping):
            return {str(key): cast(Mapping[str, Any], value) for key, value in parsed.items() if isinstance(value, Mapping)}
    return {}


def _first_manifest_layout(manifest: Mapping[str, Any]) -> Optional[Tuple[str, str, Mapping[str, Any]]]:
    profiles = manifest.get("profiles")
    if not isinstance(profiles, Mapping):
        return None
    preferred_profile_ids = ["default", *[str(key) for key in profiles.keys() if str(key) != "default"]]
    for profile_id in preferred_profile_ids:
        profile = profiles.get(profile_id)
        if not isinstance(profile, Mapping):
            continue
        layouts = profile.get("layouts")
        if not isinstance(layouts, Mapping):
            continue
        preferred_layout_ids = ["default", *[str(key) for key in layouts.keys() if str(key) != "default"]]
        for layout_id in preferred_layout_ids:
            layout = layouts.get(layout_id)
            if isinstance(layout, Mapping):
                return str(profile_id), str(layout_id), layout
    return None


def _load_manifest_runtime_boxes(manifest_path: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    if not manifest_path.exists():
        return None
    try:
        manifest_any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Failed to parse authoritative calibration manifest %s: %s", str(manifest_path), exc)
        return {}
    if not isinstance(manifest_any, Mapping):
        LOGGER.error("Authoritative calibration manifest is not a JSON object: %s", str(manifest_path))
        return {}
    if manifest_any.get("authoritative_execution_source") is not True:
        return None

    layout_info = _first_manifest_layout(manifest_any)
    if layout_info is None:
        LOGGER.error("Authoritative calibration manifest has no profile/layout records: %s", str(manifest_path))
        return {}
    profile_id, layout_id, layout = layout_info
    records: Dict[str, Any] = {}
    for key in ("required_targets", "optional_targets"):
        value = layout.get(key)
        if isinstance(value, Mapping):
            records.update(dict(value))

    source_boxes = _manifest_source_boxes(manifest_path, layout)
    runtime: Dict[str, Dict[str, Any]] = {}
    marked_targets: List[str] = []
    for manifest_target, runtime_keys in _MANIFEST_TARGET_TO_RUNTIME_BOXES.items():
        record = records.get(manifest_target)
        if not isinstance(record, Mapping):
            continue
        point, resolved_source_key, point_source = _manifest_runtime_point(
            manifest_target,
            cast(Mapping[str, Any], record),
            source_boxes,
        )
        if point is None:
            continue
        marked_targets.append(manifest_target)
        for runtime_key in runtime_keys:
            runtime[runtime_key] = {
                "x": point["x"],
                "y": point["y"],
                "calibration_source": point_source,
                "manifest_profile": profile_id,
                "manifest_layout": layout_id,
                "manifest_target": manifest_target,
                "manifest_source_key": resolved_source_key,
                "locked": True,
            }

    supplemented_targets: List[str] = []
    for runtime_key in _MANIFEST_SOURCE_TIMING_KEYS:
        if runtime_key in runtime:
            continue
        source_record: Mapping[str, Any] | None = None
        point: Optional[Dict[str, float]] = None
        resolved_source_key = ""
        for candidate in _MANIFEST_RUNTIME_TARGET_PRIORITY.get(runtime_key, (runtime_key,)):
            candidate_record = source_boxes.get(candidate)
            if not isinstance(candidate_record, Mapping):
                continue
            candidate_point = _runtime_box_point(candidate_record)
            if candidate_point is None:
                continue
            source_record = candidate_record
            point = candidate_point
            resolved_source_key = str(candidate)
            break
        if source_record is None or point is None:
            continue
        runtime[runtime_key] = {
            "x": point["x"],
            "y": point["y"],
            "calibration_source": "user_calibration_manifest_runtime_artifact",
            "manifest_profile": profile_id,
            "manifest_layout": layout_id,
            "manifest_target": runtime_key,
            "manifest_source_key": resolved_source_key or runtime_key,
            "locked": True,
        }
        supplemented_targets.append(runtime_key)

    runtime["capabilities"] = {
        "authoritative_manifest": True,
        "manifest_path": str(manifest_path),
        "manifest_profile": profile_id,
        "manifest_layout": layout_id,
        "marked_targets": marked_targets,
        "supplemented_runtime_targets": sorted(supplemented_targets),
        "runtime_targets": sorted(key for key in runtime.keys() if key != "capabilities"),
        "legacy_box_fallback_allowed": False,
    }
    required_runtime = {
        "buy_button",
        "buy_icon",
        "sell_button",
        "sell_icon",
        "time_button",
        "time_input",
        "hourly_input",
        "minute_input",
        "second_input",
    }
    missing_runtime = sorted(key for key in required_runtime if key not in runtime)
    if missing_runtime:
        runtime["capabilities"]["invalid_reason"] = f"missing_runtime_targets:{','.join(missing_runtime)}"
        runtime["capabilities"]["missing_runtime_targets"] = missing_runtime
        LOGGER.error(
            "Authoritative calibration manifest is missing runtime execution targets: %s",
            ", ".join(missing_runtime),
        )
    return runtime


def load_boxes() -> Dict[str, Dict[str, Any]]:
    manifest_boxes = _load_manifest_runtime_boxes(CALIBRATION_MANIFEST_FILE)
    if manifest_boxes is not None:
        if manifest_boxes:
            LOGGER.info(
                "Loaded authoritative user calibration manifest from %s with runtime targets: %s",
                str(CALIBRATION_MANIFEST_FILE),
                ", ".join(str(key) for key in manifest_boxes.keys() if key != "capabilities"),
            )
        return manifest_boxes

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
    3-5. hourly controls: plus, typing input, minus
    6-8. minute controls: plus, typing input, minus
    9-11. second controls: plus, typing input, minus
    12-13. buy_icon and sell_icon
    14-17. preset buttons: time_30, time_60, time_120, time_300
    18. final_screen - exit calibration
    
    NOTE: Amount is not calibrated or changed; PhoenixGuard preserves the broker's visible amount.
    """
    rect = get_window_rect(hwnd)
    if rect is None:
        raise RuntimeError("Failed to read window rectangle during calibration")

    points = [
        "broker_screen",
        "time_button",
        "hourly_plus",
        "hourly_input",
        "hourly_minus",
        "minute_plus",
        "minute_input",
        "minute_minus",
        "second_plus",
        "second_input",
        "second_minus",
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
    LOW_LEVEL_ACTION_ADAPTER.move_to_target(x, y, 120)
    LOW_LEVEL_ACTION_ADAPTER.click_target_once(x, y)
    time.sleep(pause)


def press_key(key: str) -> None:
    LOW_LEVEL_ACTION_ADAPTER.press_key(key)


def hotkey(*keys: str) -> None:
    LOW_LEVEL_ACTION_ADAPTER.hotkey(*keys)


def type_text_slowly(text: str, interval_sec: float = 0.04) -> None:
    LOW_LEVEL_ACTION_ADAPTER.type_text_slowly(text, int(max(0.0, interval_sec) * 1000))


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
        press_key("esc")
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
    capabilities = boxes.get("capabilities", {})
    if isinstance(capabilities, Mapping):
        missing_targets = capabilities.get("missing_runtime_targets")
        if missing_targets:
            LOGGER.error("Calibration invalid: authoritative manifest missing runtime targets: %s", missing_targets)
            return False
        invalid_reason = str(capabilities.get("invalid_reason") or "").strip()
        if invalid_reason:
            LOGGER.error("Calibration invalid: %s", invalid_reason)
            return False
    left, top, right, bottom = rect_bounds(rect)
    w = right - left
    h = bottom - top
    seen: List[Tuple[str, int, int]] = []
    allowed_alias_groups = (
        {"time_button", "time_input", "time_box", "expiry_time_field"},
        {"buy_icon", "buy_button"},
        {"sell_icon", "sell_button"},
        {"broker_screen", "broker_focus_area"},
        {"hourly_plus", "hour_plus", "hours_plus", "expiry_plus", "time_adjustment_plus", "hour_up"},
        {"hourly_input", "hour_input", "hours_input"},
        {"hourly_minus", "hour_minus", "hours_minus", "expiry_minus", "time_adjustment_minus", "hour_down"},
        {"minute_plus", "minutely_plus", "minutes_plus", "minute_up"},
        {"minute_input", "minutely_input", "minutes_input"},
        {"minute_minus", "minutely_minus", "minutes_minus", "minute_down"},
        {"second_plus", "seconds_plus", "second_up"},
        {"second_input", "seconds_input", "second_field", "seconds_field"},
        {"second_minus", "seconds_minus", "second_down"},
    )

    def _same_allowed_alias_group(a: str, b: str) -> bool:
        return any(a in group and b in group for group in allowed_alias_groups)

    rel_points: Dict[str, Tuple[float, float]] = {}
    for name, rel in boxes.items():
        if name == "capabilities":
            continue
        if not isinstance(rel, Mapping):
            LOGGER.error("Calibration point %s is malformed: %s", name, rel)
            return False
        rel_x = _coerce_finite_float(rel.get("x"))
        rel_y = _coerce_finite_float(rel.get("y"))
        if rel_x is None or rel_y is None:
            LOGGER.error("Calibration point %s has invalid coordinates: %s", name, rel)
            return False
        rel_points[str(name)] = (float(rel_x), float(rel_y))
        x = left + int(w * rel_x)
        y = top + int(h * rel_y)
        if x < left or x > right or y < top or y > bottom:
            LOGGER.error("Calibration point %s out of broker bounds: (%s,%s)", name, x, y)
            return False
        for other_name, ox, oy in seen:
            if abs(ox - x) < 8 and abs(oy - y) < 8:
                if _same_allowed_alias_group(name, other_name):
                    continue
                LOGGER.warning(
                    "Calibration points close: %s and %s (abs(%s,%s) vs abs(%s,%s)); aborting",
                    name,
                    other_name,
                    x,
                    y,
                    ox,
                    oy,
                )
                return False
        seen.append((name, x, y))
    layout_ok, layout_reason = _calibration_layout_reason(rel_points)
    if not layout_ok:
        LOGGER.error("Calibration invalid: %s", layout_reason)
        return False
    return True


def _calibration_layout_reason(rel_points: Mapping[str, Tuple[float, float]]) -> Tuple[bool, str]:
    def first(*keys: str) -> Optional[Tuple[float, float]]:
        for key in keys:
            point = rel_points.get(key)
            if point is not None:
                return point
        return None

    buy = first("buy_icon", "buy_button")
    sell = first("sell_icon", "sell_button")
    time_field = first("time_button", "time_input", "time_box", "expiry_time_field")
    if buy is not None and sell is not None and buy[1] >= sell[1] - 0.012:
        return False, "CALIBRATION_LAYOUT_INVALID:buy_not_above_sell"
    if time_field is not None:
        if not (0.64 <= time_field[0] <= 0.985 and 0.08 <= time_field[1] <= 0.39):
            return False, "CALIBRATION_LAYOUT_INVALID:time_field_outside_right_order_panel"
        if buy is not None and time_field[1] >= buy[1] - 0.045:
            return False, "CALIBRATION_LAYOUT_INVALID:time_not_above_trade_buttons"
    split_keys = (
        "hourly_plus",
        "hour_plus",
        "hours_plus",
        "hourly_input",
        "hour_input",
        "hours_input",
        "hourly_minus",
        "hour_minus",
        "hours_minus",
        "minute_plus",
        "minutely_plus",
        "minutes_plus",
        "minute_input",
        "minutely_input",
        "minutes_input",
        "minute_minus",
        "minutely_minus",
        "minutes_minus",
        "second_plus",
        "seconds_plus",
        "second_input",
        "seconds_input",
        "second_field",
        "seconds_field",
        "second_minus",
        "seconds_minus",
    )
    for key in split_keys:
        point = rel_points.get(key)
        if point is None:
            continue
        if not (0.48 <= point[0] <= 0.925 and 0.16 <= point[1] <= 0.48):
            return False, f"CALIBRATION_LAYOUT_INVALID:{key}:popup_control_outside_time_panel"
        if time_field is not None:
            if point[1] <= time_field[1] + 0.003:
                return False, f"CALIBRATION_LAYOUT_INVALID:{key}:popup_control_not_below_time_field"
            if point[0] >= time_field[0] + 0.015:
                return False, f"CALIBRATION_LAYOUT_INVALID:{key}:popup_control_not_left_of_time_field"
        if buy is not None and point[1] >= buy[1] - 0.060:
            return False, f"CALIBRATION_LAYOUT_INVALID:{key}:popup_control_inside_trade_button_band"
    return True, "CALIBRATION_LAYOUT_VALID"


def _binary_content_bbox(mask: Any) -> Optional[Tuple[int, int, int, int]]:
    try:
        import numpy as np
    except Exception:
        return None
    try:
        if getattr(mask, "ndim", 0) != 2:
            return None
        ys, xs = np.where(mask > 0)
        if ys.size == 0 or xs.size == 0:
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    except Exception:
        return None


@lru_cache(maxsize=1)
def _expiry_digit_template_bank() -> Dict[str, List[Any]]:
    try:
        import cv2
        import numpy as np
    except Exception:
        return {}
    fonts = (
        cv2.FONT_HERSHEY_SIMPLEX,
        cv2.FONT_HERSHEY_DUPLEX,
        cv2.FONT_HERSHEY_COMPLEX_SMALL,
        cv2.FONT_HERSHEY_TRIPLEX,
    )
    font_scales = (0.58, 0.72, 0.86, 1.00, 1.18)
    thicknesses = (1, 2, 3)
    bank: Dict[str, List[Any]] = {}
    for label in "0123456789":
        variants: List[Any] = []
        for font in fonts:
            for font_scale in font_scales:
                for thickness in thicknesses:
                    (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
                    canvas = np.zeros(
                        (
                            max(28, text_height + baseline + 14),
                            max(24, text_width + 16),
                        ),
                        dtype=np.uint8,
                    )
                    origin = (
                        max(6, (canvas.shape[1] - text_width) // 2),
                        max(text_height + 4, (canvas.shape[0] + text_height) // 2 - baseline // 2),
                    )
                    cv2.putText(canvas, label, origin, font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
                    bbox = _binary_content_bbox(canvas)
                    if bbox is None:
                        continue
                    x0, y0, x1, y1 = bbox
                    cropped = canvas[max(0, y0 - 1): min(canvas.shape[0], y1 + 1), max(0, x0 - 1): min(canvas.shape[1], x1 + 1)]
                    if getattr(cropped, "size", 0):
                        variants.append((cropped > 0).astype(np.uint8))
        try:
            from PIL import Image, ImageDraw, ImageFont

            font_paths = (
                "C:/Windows/Fonts/segoeui.ttf",
                "C:/Windows/Fonts/segoeuib.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/calibri.ttf",
                "C:/Windows/Fonts/calibrib.ttf",
                "C:/Windows/Fonts/tahoma.ttf",
                "C:/Windows/Fonts/tahomabd.ttf",
                "C:/Windows/Fonts/verdana.ttf",
                "C:/Windows/Fonts/verdanab.ttf",
            )
            for font_path in font_paths:
                if not Path(font_path).exists():
                    continue
                for size in range(10, 27):
                    try:
                        pil_font = ImageFont.truetype(font_path, size=size)
                    except Exception:
                        continue
                    scratch = Image.new("L", (44, 44), 0)
                    draw = ImageDraw.Draw(scratch)
                    text_bbox = draw.textbbox((0, 0), label, font=pil_font)
                    text_width = int(text_bbox[2] - text_bbox[0])
                    text_height = int(text_bbox[3] - text_bbox[1])
                    canvas = Image.new("L", (max(24, text_width + 16), max(28, text_height + 14)), 0)
                    draw = ImageDraw.Draw(canvas)
                    draw.text(
                        (
                            (canvas.width - text_width) // 2 - int(text_bbox[0]),
                            (canvas.height - text_height) // 2 - int(text_bbox[1]),
                        ),
                        label,
                        fill=255,
                        font=pil_font,
                    )
                    arr = np.asarray(canvas, dtype=np.uint8)
                    bbox = _binary_content_bbox(arr)
                    if bbox is None:
                        continue
                    x0, y0, x1, y1 = bbox
                    cropped = arr[max(0, y0 - 1): min(arr.shape[0], y1 + 1), max(0, x0 - 1): min(arr.shape[1], x1 + 1)]
                    if getattr(cropped, "size", 0):
                        variants.append((cropped > 0).astype(np.uint8))
        except Exception:
            pass
        bank[label] = variants
    return bank


def _score_expiry_digit(mask: Any) -> Tuple[str, float]:
    try:
        import cv2
        import numpy as np
    except Exception:
        return "", 0.0
    try:
        normalized = (mask > 0).astype(np.uint8)
        if int(np.sum(normalized > 0)) < 8:
            return "", 0.0
    except Exception:
        return "", 0.0

    best_label = ""
    best_score = 0.0
    second_best = 0.0
    for label, templates in _expiry_digit_template_bank().items():
        label_best = 0.0
        for template in templates:
            resized = cv2.resize(
                normalized.astype(np.uint8),
                (int(template.shape[1]), int(template.shape[0])),
                interpolation=cv2.INTER_NEAREST,
            )
            predicted = resized > 0
            expected = template > 0
            intersection = float(np.logical_and(predicted, expected).sum())
            predicted_area = float(max(1, predicted.sum()))
            expected_area = float(max(1, expected.sum()))
            union = float(max(1.0, np.logical_or(predicted, expected).sum()))
            precision = intersection / predicted_area
            recall = intersection / expected_area
            harmonic = 0.0 if (precision + recall) <= 1e-9 else (2.0 * precision * recall / (precision + recall))
            iou = intersection / union
            score = 0.56 * harmonic + 0.44 * iou
            if score > label_best:
                label_best = score
        if label_best > best_score:
            second_best = best_score
            best_label = label
            best_score = label_best
        elif label_best > second_best:
            second_best = label_best
    margin = max(0.0, best_score - second_best)
    confidence = max(0.0, min(1.0, 0.74 * best_score + 0.36 * margin))
    return best_label, confidence


def _read_expiry_seconds_from_time_crop(image: Any) -> Optional[int]:
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    try:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if arr.ndim != 3 or arr.size == 0:
            return None
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        mask = np.where(
            ((hsv[:, :, 1] <= 120) & (hsv[:, :, 2] >= 120)) | (gray >= 130),
            255,
            0,
        ).astype(np.uint8)
        digit_mask = mask.copy()
        digit_mask[: int(round(digit_mask.shape[0] * 0.28)), :] = 0
        digit_mask[int(round(digit_mask.shape[0] * 0.88)) :, :] = 0
        digit_mask[:, int(round(digit_mask.shape[1] * 0.76)) :] = 0
        contours, _hier = cv2.findContours(digit_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        components: List[Tuple[int, int, int, int]] = []
        for contour in contours:
            cx, cy, box_w, box_h = cv2.boundingRect(contour)
            if box_h < 7 or box_w < 2:
                continue
            if box_h > max(42, int(digit_mask.shape[0] * 0.76)) or box_w > max(30, int(digit_mask.shape[1] * 0.24)):
                continue
            if box_w >= max(13, int(round(box_h * 1.28))):
                split_count = max(2, min(3, int(round(float(box_w) / max(7.0, float(box_h) * 0.78)))))
                for split_index in range(split_count):
                    sx0 = int(round(cx + box_w * split_index / split_count))
                    sx1 = int(round(cx + box_w * (split_index + 1) / split_count))
                    if sx1 - sx0 >= 2:
                        components.append((int(sx0), int(cy), int(sx1), int(cy + box_h)))
            else:
                components.append((int(cx), int(cy), int(cx + box_w), int(cy + box_h)))
        if len(components) < 6:
            return None
        components.sort(key=lambda row: row[0])
        if len(components) > 6:
            components = components[:6]
        digits: List[str] = []
        confidences: List[float] = []
        for cx0, cy0, cx1, cy1 in components[:6]:
            crop = digit_mask[max(0, cy0 - 1): min(digit_mask.shape[0], cy1 + 1), max(0, cx0 - 1): min(digit_mask.shape[1], cx1 + 1)]
            label, confidence = _score_expiry_digit((crop > 0).astype(np.uint8))
            if not label.isdigit():
                return None
            digits.append(label)
            confidences.append(confidence)
        if len(digits) != 6:
            return None
        average_confidence = float(np.mean(np.asarray(confidences, dtype=np.float32))) if confidences else 0.0
        if average_confidence < 0.48:
            return None
        raw = "".join(digits)
        hours = int(raw[0:2])
        minutes = int(raw[2:4])
        seconds = int(raw[4:6])
        if hours > 24 or minutes > 59 or seconds > 59:
            return None
        return int(hours * 3600 + minutes * 60 + seconds)
    except Exception:
        return None


def _read_time_region_by_template(hwnd: int, boxes: Dict[str, Dict[str, Any]]) -> Optional[int]:
    rect = get_window_rect(hwnd)
    if rect is None:
        return None
    window_left, window_top, window_right, window_bottom = rect_bounds(rect)
    target_names = ("time_input", "expiry_time_field", "time_button", "time_box")

    def _bounded_region(center_x: int, center_y: int, left_pad: int, top_pad: int, right_pad: int, bottom_pad: int) -> Optional[Tuple[int, int, int, int]]:
        crop_left = max(window_left, int(center_x) - int(left_pad))
        crop_top = max(window_top, int(center_y) - int(top_pad))
        crop_right = min(window_right, int(center_x) + int(right_pad))
        crop_bottom = min(window_bottom, int(center_y) + int(bottom_pad))
        width = crop_right - crop_left
        height = crop_bottom - crop_top
        if width < 24 or height < 12:
            return None
        return crop_left, crop_top, width, height

    seen_centers: set[Tuple[int, int]] = set()
    candidates: List[int] = []
    for target_name in target_names:
        rel = boxes.get(target_name)
        if not isinstance(rel, Mapping):
            continue
        rel_x = _coerce_finite_float(rel.get("x"))
        rel_y = _coerce_finite_float(rel.get("y"))
        if rel_x is None or rel_y is None:
            continue
        x, y = rel_to_abs(rect, rel_x, rel_y)
        center_key = (int(round(x)), int(round(y)))
        if center_key in seen_centers:
            continue
        seen_centers.add(center_key)
        for region in (
            _bounded_region(x, y, 86, 28, 70, 12),
            _bounded_region(x, y, 96, 30, 76, 16),
            _bounded_region(x, y, 110, 34, 90, 22),
        ):
            if region is None:
                continue
            try:
                image = pyautogui.screenshot(region=region)
            except Exception:
                continue
            parsed = _read_expiry_seconds_from_time_crop(image)
            if parsed is not None:
                candidates.append(int(parsed))
    if not candidates:
        return None
    counts: Dict[int, int] = {}
    first_seen: Dict[int, int] = {}
    for index, parsed in enumerate(candidates):
        counts[parsed] = counts.get(parsed, 0) + 1
        first_seen.setdefault(parsed, index)
    return max(counts, key=lambda value: (counts[value], -first_seen[value]))


def ocr_read_time_region(hwnd: int, boxes: Dict[str, Dict[str, Any]]) -> Optional[int]:
    visual_seconds = _read_time_region_by_template(hwnd, boxes)
    if visual_seconds is not None:
        return visual_seconds
    if not has_ocr or pytesseract is None:
        return None
    rect = get_window_rect(hwnd)
    if rect is None:
        return None
    window_left, window_top, window_right, window_bottom = rect_bounds(rect)
    target_names = ("time_input", "expiry_time_field", "time_button", "time_box")
    ocr_config = "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789:"
    exhaustive_ocr = str(os.getenv("PHOENIXGUARD_EXHAUSTIVE_TIME_OCR", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    fast_ocr = str(os.getenv("PHOENIXGUARD_FAST_TIME_OCR", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}
    try:
        default_timeout = "0.32" if fast_ocr and not exhaustive_ocr else "0.85"
        ocr_timeout = max(0.15, min(2.0, float(os.getenv("PHOENIXGUARD_TIME_OCR_TIMEOUT_SEC", default_timeout) or default_timeout)))
    except ValueError:
        ocr_timeout = 0.32 if fast_ocr and not exhaustive_ocr else 0.85
    try:
        default_budget = "0.95" if fast_ocr and not exhaustive_ocr else "4.0"
        ocr_budget_sec = max(0.25, min(8.0, float(os.getenv("PHOENIXGUARD_TIME_OCR_BUDGET_SEC", default_budget) or default_budget)))
    except ValueError:
        ocr_budget_sec = 0.95 if fast_ocr and not exhaustive_ocr else 4.0
    deadline = time.perf_counter() + (ocr_budget_sec if not exhaustive_ocr else max(ocr_budget_sec, 4.0))

    def _bounded_region(center_x: int, center_y: int, left_pad: int, top_pad: int, right_pad: int, bottom_pad: int) -> Optional[Tuple[int, int, int, int]]:
        crop_left = max(window_left, int(center_x) - int(left_pad))
        crop_top = max(window_top, int(center_y) - int(top_pad))
        crop_right = min(window_right, int(center_x) + int(right_pad))
        crop_bottom = min(window_bottom, int(center_y) + int(bottom_pad))
        width = crop_right - crop_left
        height = crop_bottom - crop_top
        if width < 24 or height < 12:
            return None
        return crop_left, crop_top, width, height

    def _image_variants(img: Any) -> List[Any]:
        try:
            from PIL import ImageEnhance, ImageOps

            gray = ImageOps.grayscale(img)
            width, height = gray.size
            scaled = gray.resize((max(1, width * 4), max(1, height * 4)))
            contrast = ImageEnhance.Contrast(scaled).enhance(2.2)
            threshold = contrast.point(lambda px: 255 if int(px) > 145 else 0)
            if exhaustive_ocr:
                return [contrast, threshold, scaled, img]
            if fast_ocr:
                return [threshold, contrast]
            return [contrast, threshold]
        except Exception:
            return [img]

    seen_centers: set[Tuple[int, int]] = set()
    for target_name in target_names:
        rel = boxes.get(target_name)
        if not isinstance(rel, Mapping):
            continue
        rel_x = _coerce_finite_float(rel.get("x"))
        rel_y = _coerce_finite_float(rel.get("y"))
        if rel_x is None or rel_y is None:
            continue
        x, y = rel_to_abs(rect, rel_x, rel_y)
        center_key = (int(round(x)), int(round(y)))
        if center_key in seen_centers:
            continue
        seen_centers.add(center_key)
        regions = [
            _bounded_region(x, y, 86, 28, 70, 12),
            _bounded_region(x, y, 96, 30, 76, 16),
            _bounded_region(x, y, 110, 34, 90, 22),
        ]
        if not exhaustive_ocr:
            regions = regions[:1]
        parsed_candidates: List[int] = []
        for region in regions:
            if region is None:
                continue
            if not exhaustive_ocr and time.perf_counter() >= deadline:
                break
            try:
                img = pyautogui.screenshot(region=region)
            except Exception:
                continue
            for variant in _image_variants(img):
                if not exhaustive_ocr and time.perf_counter() >= deadline:
                    break
                try:
                    try:
                        txt_raw = pytesseract.image_to_string(variant, config=ocr_config, timeout=ocr_timeout)
                    except TypeError:
                        if not exhaustive_ocr:
                            continue
                        txt_raw = pytesseract.image_to_string(variant, config=ocr_config)
                    txt = txt_raw if isinstance(txt_raw, str) else str(txt_raw)
                    parsed = _parse_visible_time_seconds(txt)
                    if parsed is not None:
                        parsed_candidates.append(int(parsed))
                except Exception:
                    continue
        if parsed_candidates:
            counts: Dict[int, int] = {}
            first_seen: Dict[int, int] = {}
            for index, parsed in enumerate(parsed_candidates):
                counts[parsed] = counts.get(parsed, 0) + 1
                first_seen.setdefault(parsed, index)
            return max(counts, key=lambda value: (counts[value], -first_seen[value]))
        if not exhaustive_ocr and fast_ocr and time.perf_counter() >= deadline:
            return None
    return None


def set_amount(hwnd: int, boxes: Dict[str, Dict[str, Any]], amount: int) -> None:
    _ = (hwnd, boxes, amount)
    LOGGER.info("set_amount: skipped; PhoenixGuard preserves the broker's visible amount.")


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



# ------------------------------------------------------------------
# THREE-GATE SHOOTER MODE
# ------------------------------------------------------------------
# The shooter no longer uses strict swing gates, memory-confidence blocks,
# source-preference blocks, hard-coded sniper state, broker-lane logic, or
# skill-gate pass counts as live execution authority.
#
# Live authority is reduced to:
#   Gate 1: second-latest-read confirmation
#   Gate 2: five trades, then twenty-minute safety wait
#   Gate 3: adaptive opposing-force distance
#
# Mechanical requirements still remain because the shooter physically needs a
# side, an expiry, a calibrated button path, and a live broker window to click.

THREE_GATE_STATE_FILE = Path("shooter_3_gate_state.json")
THREE_GATE_TRADE_LIMIT = 5
THREE_GATE_LOCK_SECONDS = 20 * 60
THREE_GATE_DEFAULT_FORCE_MULTIPLIER = 1.25
THREE_GATE_DEFAULT_VISIBLE_RANGE_FRACTION = 0.08
THREE_GATE_DEFAULT_NORMALIZED_DISTANCE_FRACTION = 0.08

PG_EXECUTION_PACKET_SCHEMA_V3 = "PG_EXECUTION_PACKET_V3"
PG_MODEL_COUNCIL_STUDY_SCHEMA_V3 = "PG_MODEL_COUNCIL_STUDY_V3"
PG_SHOOTER_RUNTIME_VERSION = "PG_SHOOTER_V3_MODEL_COUNCIL_ONLY"
V3_EXECUTED_PACKET_LIMIT = 500
V3_SECOND_READ_BASELINE_MAX_AGE_SECONDS = 30.0
DEFAULT_V3_SHOOTER_MODE = shooter_modes.DEFAULT_SHOOTER_MODE.value
LIVE_BROKER_CLICK_ENV = "PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS"


def _nested_get(mapping: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _median_float(values: Sequence[float]) -> Optional[float]:
    clean = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not clean:
        return None
    n = len(clean)
    mid = n // 2
    if n % 2:
        return float(clean[mid])
    return float((clean[mid - 1] + clean[mid]) / 2.0)


def _resolve_three_gate_side(payload: Dict[str, Any]) -> Tuple[Optional[str], str, str, Any]:
    """Resolve BUY/SELL without requiring actionable=True or decision_kernel."""
    direct_paths: list[tuple[str, Sequence[str]]] = [
        ("execution_action", ("execution_action",)),
        ("action", ("action",)),
        ("side", ("side",)),
        ("candidate_action", ("candidate_action",)),
        ("signal_armed_action", ("signal_armed_action",)),
        ("best_play_action", ("best_play_action",)),
        ("model_action", ("model_action",)),
        ("direction", ("direction",)),
        ("thesis_action", ("thesis_action",)),
        ("phase_bias", ("phase_bias",)),
        ("broker_execution_state.side", ("broker_execution_state", "side")),
        ("broker_execution_state.action", ("broker_execution_state", "action")),
        ("execution_contract.side", ("execution_contract", "side")),
        ("execution_contract.action", ("execution_contract", "action")),
        ("decision.side", ("decision", "side")),
        ("decision.action", ("decision", "action")),
        ("tracking_summary.local_direction", ("tracking_summary", "local_direction")),
        ("tracking_summary.global_direction", ("tracking_summary", "global_direction")),
        ("decision_kernel.dominant_side", ("decision_kernel", "dominant_side")),
        ("decision_kernel.major_trend_side", ("decision_kernel", "major_trend_side")),
        ("decision_kernel.next_candle_bias", ("decision_kernel", "next_candle_bias")),
    ]
    for label, path in direct_paths:
        raw = _nested_get(payload, path)
        side = _normalize_trade_side(raw)
        if side in {"BUY", "SELL"}:
            return side, f"field({label})", label, raw

    # Last-resort bias readers are diagnostics, not gates.
    try:
        bias_side, bias_source = _extract_bias_side(payload)
        if bias_side in {"BUY", "SELL"}:
            return bias_side, bias_source or "bias", bias_source or "bias", bias_side
    except Exception:
        pass
    return None, "none", "n/a", None


def _three_gate_expiry_candidates(payload: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """Expiry candidates accepted by the reduced shooter.

    This intentionally includes nested broker/tracker fields because the
    dashboard showed expiry arriving through broker_execution_state.
    """
    candidates: List[Tuple[str, Any]] = []
    paths: list[tuple[str, Sequence[str]]] = [
        ("expiry_seconds", ("expiry_seconds",)),
        ("expiry_sec", ("expiry_sec",)),
        ("required_seconds", ("required_seconds",)),
        ("expiry", ("expiry",)),
        ("duration_seconds", ("duration_seconds",)),
        ("broker_execution_state.expiry_seconds", ("broker_execution_state", "expiry_seconds")),
        ("broker_execution_state.expiry_sec", ("broker_execution_state", "expiry_sec")),
        ("broker_execution_state.required_seconds", ("broker_execution_state", "required_seconds")),
        ("broker_execution_state.duration_seconds", ("broker_execution_state", "duration_seconds")),
        ("execution_contract.expiry_seconds", ("execution_contract", "expiry_seconds")),
        ("execution_contract.required_seconds", ("execution_contract", "required_seconds")),
        ("execution.expiry_seconds", ("execution", "expiry_seconds")),
        ("trade.expiry_seconds", ("trade", "expiry_seconds")),
        ("timing_signal.expiry_seconds", ("timing_signal", "expiry_seconds")),
        ("timing.expiry_seconds", ("timing", "expiry_seconds")),
        ("countdown_seconds", ("countdown_seconds",)),
    ]
    for label, path in paths:
        value = _nested_get(payload, path)
        if not _is_missing_value(value):
            candidates.append((label, value))
    return candidates


def _resolve_three_gate_expiry(payload: Dict[str, Any], fallback_expiry: int = 0) -> Tuple[int, str, str, Any]:
    for field_name, raw in _three_gate_expiry_candidates(payload):
        parsed = _parse_expiry_seconds_value(raw)
        if parsed is not None and parsed > 0:
            expiry = int(max(DEFAULT_MIN_EXPIRY, min(DEFAULT_MAX_EXPIRY, parsed)))
            return expiry, f"signal_field({field_name})", field_name, raw
    # No hard-coded market timing: fallback is only used when the operator
    # explicitly supplied --expiry and no tracker expiry exists.
    if int(fallback_expiry or 0) > 0:
        expiry = int(max(DEFAULT_MIN_EXPIRY, min(DEFAULT_MAX_EXPIRY, int(fallback_expiry))))
        return expiry, "operator_fallback(--expiry)", "--expiry", fallback_expiry
    return 0, "missing", "n/a", None


def _three_gate_signal_identity(payload: Dict[str, Any], side: str, expiry: int) -> str:
    """Stable identity used for duplicate prevention and second-read logs."""
    primary = str(payload.get("signal_id") or payload.get("id") or payload.get("bundle_id") or "").strip()
    frame = str(
        payload.get("tracker_frame_id")
        or payload.get("frame_id")
        or payload.get("frame_index")
        or payload.get("capture_count")
        or payload.get("state_version")
        or payload.get("decision_version")
        or payload.get("published_epoch")
        or payload.get("updated_epoch")
        or ""
    ).strip()
    if primary and frame:
        return f"{primary}|frame={frame}|{side}|{expiry}"
    if primary:
        return primary
    fallback = _payload_identity_key(payload)
    return f"{fallback or 'no-id'}|{side}|{expiry}"


def parse_trade_signal(payload: Dict[str, Any]) -> Optional[Tuple[str, int, str, str, str, Any, str, Any]]:
    """Return a minimal executable BUY/SELL contract.

    Legacy helper retained for tests and manual diagnostics. Live V3 signal
    mode does not call this function. Keep this helper stricter than old raw
    tracker parsing: only explicit execution_action BUY/SELL is accepted.
    """
    try:
        primary_id = str(payload.get("signal_id") or payload.get("id") or payload.get("bundle_id") or "").strip()
        if not primary_id:
            LOGGER.debug("parse_signal: missing explicit signal_id/id/bundle_id")
            return None

        side_raw_field = "execution_action"
        side_raw_value = payload.get("execution_action")
        side = _normalize_trade_side(side_raw_value)
        if side not in {"BUY", "SELL"}:
            LOGGER.debug("parse_signal: no explicit BUY/SELL execution_action found")
            return None
        side_source = "field(execution_action)"

        broker_state = payload.get("broker_execution_state")
        if isinstance(broker_state, Mapping):
            broker_status = str(broker_state.get("status") or "").strip().lower()
            broker_actionable = broker_state.get("actionable")
            broker_side = _normalize_trade_side(broker_state.get("side"))
            if broker_actionable is False or broker_status in {"watching", "wait", "waiting", "blocked", "cooldown"}:
                LOGGER.debug("parse_signal: broker execution state is not actionable: %s", broker_status or broker_actionable)
                return None
            if broker_side in {"BUY", "SELL"} and broker_side != side:
                LOGGER.debug("parse_signal: broker side mismatch: %s != %s", broker_side, side)
                return None

        entry_state_text = str(payload.get("entry_state") or payload.get("setup_state") or "").strip().upper()
        if entry_state_text and any(token in entry_state_text for token in ("WATCH", "WAIT", "FORMING", "OBSERV")):
            LOGGER.debug("parse_signal: entry/setup state is not executable: %s", entry_state_text)
            return None

        kernel = payload.get("decision_kernel")
        if isinstance(kernel, Mapping):
            trade_mode = str(kernel.get("trade_mode") or "").strip().upper()
            target_horizon = _coerce_nonnegative_seconds(kernel.get("target_horizon_candles"))
            if trade_mode in {"COUNTERTREND_SCALP", "SCALP", "MICRO_SCALP"}:
                LOGGER.debug("parse_signal: rejecting non-swing trade_mode=%s", trade_mode)
                return None
            if target_horizon is not None and target_horizon > 0 and target_horizon < 4:
                LOGGER.debug("parse_signal: rejecting short target_horizon_candles=%s", target_horizon)
                return None

        fallback_expiry = int(payload.get("_operator_fallback_expiry", 0) or 0)
        expiry, expiry_source, expiry_raw_field, expiry_raw_value = _resolve_three_gate_expiry(payload, fallback_expiry)
        if expiry <= 0:
            LOGGER.info("parse_signal: tracker read has side=%s but no usable expiry", side)
            return None

        signal_id = _three_gate_signal_identity(payload, side, expiry)
        LOGGER.info(
            "parse_signal: accepted tracker read side=%s expiry=%ss side_from=%s expiry_from=%s id=%s",
            side,
            expiry,
            side_source,
            expiry_source,
            signal_id,
        )
        return side, expiry, signal_id, expiry_source, expiry_raw_field, expiry_raw_value, side_raw_field, side_raw_value
    except Exception as exc:
        LOGGER.error("parse_signal: unexpected error: %s", exc)
        return None


def _three_gate_load_state() -> Dict[str, Any]:
    if not THREE_GATE_STATE_FILE.exists():
        return {"trade_count": 0, "locked_until": 0.0, "executed_keys": []}
    try:
        parsed = json.loads(THREE_GATE_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            parsed.setdefault("trade_count", 0)
            parsed.setdefault("locked_until", 0.0)
            parsed.setdefault("executed_keys", [])
            return cast(Dict[str, Any], parsed)
    except Exception as exc:
        LOGGER.warning("3-gate state load failed: %s", exc)
    return {"trade_count": 0, "locked_until": 0.0, "executed_keys": []}


def _three_gate_save_state(state: Mapping[str, Any]) -> None:
    try:
        THREE_GATE_STATE_FILE.write_text(json.dumps(dict(state), indent=2), encoding="utf-8")
    except Exception as exc:
        LOGGER.warning("3-gate state save failed: %s", exc)


def _gate1_second_latest_read(
    state: Dict[str, Any],
    payload: Dict[str, Any],
    side: str,
    expiry: int,
    signal_key: str,
) -> Tuple[bool, str]:
    """Gate 1: first read or changed side stores orientation; second read can trade."""
    baseline = state.get("baseline")
    if not isinstance(baseline, dict):
        state["baseline"] = {
            "side": side,
            "expiry": int(expiry),
            "signal_key": signal_key,
            "seen_at": time.time(),
            "payload_identity": _payload_identity_key(payload),
        }
        return False, "Gate 1 WAITING_SECOND_READ: first tracker read stored"

    prev_side = _normalize_trade_side(baseline.get("side"))
    prev_expiry = int(baseline.get("expiry") or 0)
    if prev_side != side:
        state["baseline"] = {
            "side": side,
            "expiry": int(expiry),
            "signal_key": signal_key,
            "seen_at": time.time(),
            "payload_identity": _payload_identity_key(payload),
        }
        return False, f"Gate 1 WAITING_SECOND_READ: side changed {prev_side}->{side}; baseline reset"

    if prev_expiry > 0 and expiry > 0 and abs(prev_expiry - expiry) > max(5, int(prev_expiry * 0.25)):
        state["baseline"] = {
            "side": side,
            "expiry": int(expiry),
            "signal_key": signal_key,
            "seen_at": time.time(),
            "payload_identity": _payload_identity_key(payload),
        }
        return False, f"Gate 1 WAITING_SECOND_READ: expiry changed {prev_expiry}->{expiry}; baseline reset"

    return True, "Gate 1 PASS: second tracker read confirmed"


def _gate2_trade_wait(state: Dict[str, Any], now: float) -> Tuple[bool, str, int]:
    """Gate 2: after five executions, wait twenty minutes."""
    locked_until = float(state.get("locked_until", 0.0) or 0.0)
    remaining = max(0, int(locked_until - now))
    if remaining > 0:
        return False, f"Gate 2 TWENTY_MINUTE_WAIT: {remaining}s remaining", remaining
    return True, "Gate 2 PASS: trade safety window open", 0


def _three_gate_extract_price_rows(payload: Mapping[str, Any]) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []

    def _consume_row(raw: Any) -> None:
        if not isinstance(raw, Mapping):
            return
        row: Dict[str, float] = {}
        key_map = {
            "open": ("open", "o", "open_proxy"),
            "high": ("high", "h", "high_proxy"),
            "low": ("low", "l", "low_proxy"),
            "close": ("close", "c", "close_proxy", "price_proxy"),
        }
        for out_key, candidates in key_map.items():
            for candidate in candidates:
                parsed = _coerce_finite_float(raw.get(candidate))
                if parsed is not None:
                    row[out_key] = float(parsed)
                    break
        if row:
            rows.append(row)

    containers: List[Any] = [
        payload.get("tracked_candles"),
        payload.get("candles"),
        payload.get("candle_history"),
        payload.get("ohlc"),
        _nested_get(payload, ("tracking_summary", "tracked_candles")),
        _nested_get(payload, ("tracking_summary", "candles")),
        _nested_get(payload, ("chart_state", "tracked_candles")),
        _nested_get(payload, ("chart_state", "candles")),
    ]
    for container in containers:
        if isinstance(container, Sequence) and not isinstance(container, (str, bytes, bytearray)):
            for item in container:
                _consume_row(item)
    return rows


def _three_gate_current_price(payload: Mapping[str, Any], rows: Sequence[Mapping[str, float]]) -> Optional[float]:
    paths: list[Sequence[str]] = [
        ("current_price",),
        ("latest_price",),
        ("price",),
        ("close",),
        ("price_proxy",),
        ("latest_price_proxy",),
        ("tracking_summary", "current_price"),
        ("tracking_summary", "latest_price"),
        ("tracking_summary", "latest_price_proxy"),
        ("chart_state", "current_price"),
        ("chart_state", "latest_price"),
    ]
    for path in paths:
        parsed = _coerce_finite_float(_nested_get(payload, path))
        if parsed is not None:
            return float(parsed)
    if rows:
        for key in ("close", "price", "high", "low", "open"):
            parsed = _coerce_finite_float(rows[-1].get(key))
            if parsed is not None:
                return float(parsed)
    return None


def _zone_containers(payload: Mapping[str, Any]) -> List[Any]:
    return [
        payload.get("support_resistance_zones"),
        payload.get("zones"),
        payload.get("significant_zones"),
        payload.get("supply_demand_zones"),
        _nested_get(payload, ("tracking_summary", "support_resistance_zones")),
        _nested_get(payload, ("tracking_summary", "zones")),
        _nested_get(payload, ("tracking_summary", "significant_zones")),
        _nested_get(payload, ("chart_state", "zones")),
        _nested_get(payload, ("zone_learning", "zones")),
    ]


def _zone_price_candidates(zone: Mapping[str, Any]) -> List[float]:
    values: List[float] = []
    for key in ("price", "level", "center", "mid", "middle", "y", "value", "top", "bottom", "high", "low"):
        parsed = _coerce_finite_float(zone.get(key))
        if parsed is not None:
            values.append(float(parsed))
    bbox = zone.get("bbox")
    if isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes, bytearray)) and len(bbox) >= 4:
        nums = [_coerce_finite_float(v) for v in bbox[:4]]
        nums2 = [float(v) for v in nums if v is not None]
        if len(nums2) == 4:
            values.append(float((nums2[1] + nums2[3]) / 2.0))
    return values


def _zone_matches_opposing_force(zone: Mapping[str, Any], side: str) -> bool:
    text = " ".join(
        str(zone.get(key, "") or "").strip().lower()
        for key in ("role", "label", "name", "kind", "type", "zone_type", "direction", "entry_relevance", "price_relation")
    )
    direction = _normalize_trade_side(zone.get("direction") or zone.get("candidate_side"))
    if side == "BUY":
        return (
            direction == "SELL"
            or "resistance" in text
            or "sell" in text
            or "supply" in text
            or "upper" in text
        )
    return (
        direction == "BUY"
        or "support" in text
        or "buy" in text
        or "demand" in text
        or "lower" in text
    )


def _three_gate_opposing_zone_prices(payload: Mapping[str, Any], side: str) -> List[float]:
    prices: List[float] = []
    for container in _zone_containers(payload):
        if isinstance(container, Mapping):
            iterable = container.values()
        elif isinstance(container, Sequence) and not isinstance(container, (str, bytes, bytearray)):
            iterable = container
        else:
            continue
        for item in iterable:
            if not isinstance(item, Mapping):
                continue
            if _zone_matches_opposing_force(item, side):
                prices.extend(_zone_price_candidates(item))
    return [float(p) for p in prices if math.isfinite(float(p))]


def _three_gate_normalized_close_position(payload: Mapping[str, Any], rows: Sequence[Mapping[str, float]], current_price: Optional[float]) -> Optional[float]:
    for path in [
        ("close_position",),
        ("position_in_range",),
        ("current_position_norm",),
        ("tracking_summary", "close_position"),
        ("tracking_summary", "position_in_range"),
        ("chart_state", "close_position"),
    ]:
        parsed = _clip_unit_float(_nested_get(payload, path))
        if parsed is not None:
            return float(parsed)
    values: List[float] = []
    for row in rows:
        for key in ("high", "low", "close", "open"):
            parsed = _coerce_finite_float(row.get(key))
            if parsed is not None:
                values.append(float(parsed))
    if current_price is not None and values:
        return _position_value_in_price_values(float(current_price), values)
    return None


def _gate3_opposing_force_distance(
    payload: Dict[str, Any],
    side: str,
    *,
    force_multiplier: float = THREE_GATE_DEFAULT_FORCE_MULTIPLIER,
    visible_range_fraction: float = THREE_GATE_DEFAULT_VISIBLE_RANGE_FRACTION,
    normalized_distance_fraction: float = THREE_GATE_DEFAULT_NORMALIZED_DISTANCE_FRACTION,
) -> Tuple[bool, str]:
    """Gate 3: block only when the setup is too close to the opposing force.

    BUY can be taken in the middle if the nearest high/resistance/sell zone is
    far enough. SELL can be taken in the middle if the nearest low/support/buy
    zone is far enough.
    """
    rows = _three_gate_extract_price_rows(payload)
    current = _three_gate_current_price(payload, rows)

    range_values: List[float] = []
    candle_ranges: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    for row in rows:
        high = _coerce_finite_float(row.get("high"))
        low = _coerce_finite_float(row.get("low"))
        close = _coerce_finite_float(row.get("close"))
        open_ = _coerce_finite_float(row.get("open"))
        for value in (high, low, close, open_):
            if value is not None:
                range_values.append(float(value))
        if high is not None:
            highs.append(float(high))
        if low is not None:
            lows.append(float(low))
        if high is not None and low is not None and high >= low:
            candle_ranges.append(float(high - low))

    zone_prices = _three_gate_opposing_zone_prices(payload, side)
    if current is not None:
        if side == "BUY":
            opposing_candidates = [p for p in zone_prices + highs if p > current]
            relation = "resistance/high/sell-zone"
        else:
            opposing_candidates = [p for p in zone_prices + lows if p < current]
            relation = "support/low/buy-zone"

        if opposing_candidates and range_values:
            nearest = min(opposing_candidates, key=lambda p: abs(float(p) - float(current)))
            distance = abs(float(nearest) - float(current))
            visible_range = max(range_values) - min(range_values)
            median_range = _median_float([r for r in candle_ranges if r > 0.0])
            adaptive_min = max(
                float(median_range or 0.0) * max(0.1, float(force_multiplier)),
                float(visible_range or 0.0) * max(0.0, float(visible_range_fraction)),
            )
            if adaptive_min <= 0.0:
                adaptive_min = distance * 0.5
            if distance < adaptive_min:
                return (
                    False,
                    f"Gate 3 OPPOSING_FORCE_BLOCK: {side} is {distance:.6g} from {relation}; adaptive minimum {adaptive_min:.6g}",
                )
            return (
                True,
                f"Gate 3 PASS: {side} distance to nearest {relation} is {distance:.6g} >= adaptive minimum {adaptive_min:.6g}",
            )

        if not opposing_candidates:
            return True, f"Gate 3 PASS: no opposing {relation} found ahead of current price"

    # Normalized fallback allows middle trades when not close to opposing force.
    close_position = _three_gate_normalized_close_position(payload, rows, current)
    if close_position is not None:
        if side == "BUY":
            distance_norm = 1.0 - float(close_position)
            relation = "upper opposing force"
        else:
            distance_norm = float(close_position)
            relation = "lower opposing force"
        threshold = max(0.01, min(0.45, float(normalized_distance_fraction)))
        if distance_norm < threshold:
            return (
                False,
                f"Gate 3 OPPOSING_FORCE_BLOCK: {side} too close to {relation}; distance_norm={distance_norm:.3f} threshold={threshold:.3f}",
            )
        return (
            True,
            f"Gate 3 PASS: {side} normalized distance from {relation}={distance_norm:.3f} threshold={threshold:.3f}",
        )

    # Reduced system: absence of zone data is not treated as a hardcoded market block.
    return True, "Gate 3 PASS: no opposing-force data available; shooter will not invent a block"


def _three_gate_record_execution(state: Dict[str, Any], signal_key: str, now: float) -> Tuple[int, int]:
    executed_keys = state.get("executed_keys")
    if not isinstance(executed_keys, list):
        executed_keys = []
    executed_keys.append(signal_key)
    state["executed_keys"] = executed_keys[-200:]
    trade_count = int(state.get("trade_count", 0) or 0) + 1
    state["trade_count"] = trade_count
    locked_for = 0
    if trade_count >= THREE_GATE_TRADE_LIMIT:
        locked_for = THREE_GATE_LOCK_SECONDS
        state["locked_until"] = now + float(locked_for)
        state["trade_count"] = 0
        # Require a new second-read sequence after a 5-trade safety lock.
        state.pop("baseline", None)
    _three_gate_save_state(state)
    return int(state.get("trade_count", 0) or 0), locked_for


def _three_gate_already_executed(state: Mapping[str, Any], signal_key: str) -> bool:
    executed_keys = state.get("executed_keys")
    if not isinstance(executed_keys, list):
        return False
    return signal_key in {str(k) for k in executed_keys[-200:]}


# ------------------------------------------------------------------
# PHOENIXGUARD V3 MODEL COUNCIL SHOOTER MODE
# ------------------------------------------------------------------
# Live signal mode must consume only PG_EXECUTION_PACKET_V3 packets published
# by the Model Council. Legacy parsers above remain for compatibility tests and
# manual diagnostics, but the live loop below routes through these helpers.

def _v3_mapping(value: Any) -> Dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _v3_execution_lane_context(
    packet: Mapping[str, Any],
    council: Optional[Mapping[str, Any]] = None,
    promotion: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    council_payload = _v3_mapping(council)
    promotion_payload = _v3_mapping(promotion)
    lane_payload = _v3_mapping(
        packet.get("execution_lane")
        or council_payload.get("execution_lane")
        or promotion_payload.get("execution_lane")
    )
    lane_name = str(
        lane_payload.get("name")
        or packet.get("selected_execution_lane")
        or council_payload.get("selected_execution_lane")
        or promotion_payload.get("selected_lane")
        or ""
    ).strip()
    lane_accepted_raw = (
        promotion_payload.get("lane_accepted")
        if promotion_payload.get("lane_accepted") is not None
        else lane_payload.get("accepted")
    )
    accepted_lanes = (
        lane_payload.get("accepted_lanes")
        or promotion_payload.get("accepted_lanes")
        or packet.get("accepted_lanes")
        or []
    )
    if not isinstance(accepted_lanes, list):
        accepted_lanes = []
    current_candle = _v3_mapping(
        packet.get("current_candle_acceptance")
        or council_payload.get("current_candle_acceptance")
        or promotion_payload.get("current_candle_acceptance")
        or lane_payload.get("current_candle_acceptance")
    )
    return {
        "selected_execution_lane": lane_name,
        "execution_lane": lane_payload,
        "lane_accepted": lane_accepted_raw if isinstance(lane_accepted_raw, bool) else None,
        "accepted_lanes": [str(lane) for lane in accepted_lanes if str(lane or "").strip()],
        "current_candle_acceptance": current_candle,
    }


def _v3_packet_id(packet: Mapping[str, Any]) -> str:
    return str(packet.get("packet_id") or packet.get("decision_id") or "").strip()


def _v3_packet_side(packet: Mapping[str, Any]) -> Optional[str]:
    execution = _v3_mapping(packet.get("execution"))
    return _normalize_trade_side(execution.get("side"))


def _v3_packet_expiry_seconds(packet: Mapping[str, Any]) -> int:
    execution = _v3_mapping(packet.get("execution"))
    time_sequence = _v3_mapping(execution.get("time_sequence"))
    for raw in (
        execution.get("expiry_seconds"),
        time_sequence.get("target_seconds"),
        time_sequence.get("target_text"),
    ):
        parsed = _parse_expiry_seconds_value(raw)
        if parsed is not None and parsed > 0:
            return int(parsed)
    return 0


def _v3_packet_counter(packet: Mapping[str, Any], key: str) -> Optional[int]:
    try:
        value = packet.get(key)
        if value in (None, "", "n/a", "N/A"):
            return None
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except Exception:
        return None


def _v3_live_hash(packet: Mapping[str, Any]) -> str:
    live_integrity = _v3_mapping(packet.get("live_integrity"))
    return str(
        live_integrity.get("input_frame_hash")
        or packet.get("input_frame_hash")
        or ""
    ).strip()


def _v3_packet_execution_key(packet: Mapping[str, Any]) -> str:
    packet_id = _v3_packet_id(packet)
    if packet_id:
        return packet_id
    side = _v3_packet_side(packet) or "NO_SIDE"
    parts = [
        str(packet.get("session_id") or ""),
        str(packet.get("symbol") or ""),
        str(packet.get("timeframe") or ""),
        str(packet.get("frame_id") or ""),
        str(packet.get("capture_count") or ""),
        str(packet.get("state_version") or ""),
        side,
        _v3_live_hash(packet),
    ]
    return "|".join(parts)


def _v3_packet_identity(packet: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": str(packet.get("session_id") or "").strip(),
        "symbol": str(packet.get("symbol") or "").strip(),
        "timeframe": str(packet.get("timeframe") or "").strip(),
        "frame_id": _v3_packet_counter(packet, "frame_id"),
        "capture_count": _v3_packet_counter(packet, "capture_count"),
        "state_version": _v3_packet_counter(packet, "state_version"),
        "packet_id": _v3_packet_id(packet),
        "side": _v3_packet_side(packet),
        "input_frame_hash": _v3_live_hash(packet),
    }


def _v3_live_read_identity_from_tracker(tracker_snapshot: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(tracker_snapshot, Mapping):
        return {}
    latest_signal = _v3_mapping(tracker_snapshot.get("latest_signal"))
    tracking_summary = _v3_mapping(tracker_snapshot.get("tracking_summary"))
    live_integrity = _v3_mapping(latest_signal.get("live_integrity") or tracking_summary.get("live_integrity"))
    frame_id = _first_visible_value(
        tracker_snapshot.get("display_frame_id"),
        tracker_snapshot.get("frame_id"),
        tracker_snapshot.get("frame_index"),
        tracker_snapshot.get("chart_frame_id"),
        tracker_snapshot.get("overlay_frame_id"),
        tracker_snapshot.get("full_overlay_frame_id"),
    )
    return {
        "session_id": str(tracker_snapshot.get("session_id") or "").strip(),
        "symbol": str(
            tracker_snapshot.get("symbol")
            or latest_signal.get("symbol")
            or latest_signal.get("market")
            or tracking_summary.get("detected_market")
            or ""
        ).strip(),
        "timeframe": str(
            tracker_snapshot.get("timeframe")
            or latest_signal.get("timeframe")
            or latest_signal.get("focus_timeframe")
            or tracking_summary.get("detected_timeframe")
            or ""
        ).strip(),
        "frame_id": _v3_packet_counter({"frame_id": frame_id}, "frame_id"),
        "capture_count": _v3_packet_counter(tracker_snapshot, "capture_count"),
        "state_version": _v3_packet_counter(
            {
                "state_version": _first_visible_value(
                    tracker_snapshot.get("state_version"),
                    tracker_snapshot.get("decision_version"),
                    latest_signal.get("state_version"),
                    tracking_summary.get("state_version"),
                )
            },
            "state_version",
        ),
        "input_frame_hash": str(
            live_integrity.get("input_frame_hash")
            or latest_signal.get("input_frame_hash")
            or tracking_summary.get("input_frame_hash")
            or ""
        ).strip(),
    }


def _v3_base_decision(packet: Optional[Mapping[str, Any]], now: Optional[float] = None) -> Dict[str, Any]:
    timestamp = float(time.time() if now is None else now)
    side = _v3_packet_side(packet) if isinstance(packet, Mapping) else None
    return {
        "timestamp": timestamp,
        "packet_id": _v3_packet_id(packet) if isinstance(packet, Mapping) else None,
        "will_click": False,
        "side": side,
        "reason": "NOT_EVALUATED",
        "runtime_integrity": "NOT_CHECKED",
        "gate_1_second_read": "NOT_CHECKED",
        "gate_2_trade_discipline": "NOT_CHECKED",
        "gate_3_model_council": "NOT_CHECKED",
        "calibration": "NOT_CHECKED",
        "shooter_runtime_version": PG_SHOOTER_RUNTIME_VERSION,
    }


def _v3_study_wait_decision(study_packet: Mapping[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    decision = _v3_base_decision(study_packet, now)
    execution = _v3_mapping(study_packet.get("execution"))
    council = _v3_mapping(study_packet.get("model_council"))
    promotion = _v3_mapping(study_packet.get("promotion_trace"))
    lane_context = _v3_execution_lane_context(study_packet, council, promotion)
    candidate_queue = _v3_mapping(study_packet.get("trade_candidate_queue"))
    active_candidate = _v3_mapping(candidate_queue.get("active_candidate"))
    side = (
        _normalize_trade_side(execution.get("side"))
        or _normalize_trade_side(council.get("final_side"))
        or _normalize_trade_side(promotion.get("candidate_side"))
        or _normalize_trade_side(candidate_queue.get("candidate_side"))
    )
    final_score = (
        council.get("final_execution_score")
        if council.get("final_execution_score") not in (None, "")
        else study_packet.get("final_execution_score")
        if study_packet.get("final_execution_score") not in (None, "")
        else promotion.get("final_execution_score")
    )
    threshold = (
        council.get("execution_threshold")
        if council.get("execution_threshold") not in (None, "")
        else study_packet.get("execution_threshold")
        if study_packet.get("execution_threshold") not in (None, "")
        else promotion.get("execution_threshold")
    )
    true_blocker = str(
        study_packet.get("true_blocker")
        or promotion.get("true_blocker")
        or promotion.get("blocked_by")
        or study_packet.get("block_reason")
        or council.get("true_blocker")
        or council.get("arbitration_reason")
        or "EXECUTION_PACKET_NOT_PUBLISHED"
    ).strip()
    execution_state = str(execution.get("state") or council.get("final_state") or "WATCHING").strip().upper()
    if not bool(execution.get("enabled")) and execution_state in {
        "EXECUTABLE",
        "EXECUTABLE_PACKET",
        "EXECUTION_READY",
        "READY_TO_EXECUTE",
    }:
        execution_state = "WATCHING"
    decision.update(
        {
            "packet_id": _v3_packet_id(study_packet) or str(study_packet.get("packet_id") or ""),
            "packet_type": "STUDY_PACKET",
            "execution_state": execution_state,
            "side": side,
            "candidate_id": str(
                promotion.get("candidate_id")
                or candidate_queue.get("candidate_id")
                or active_candidate.get("candidate_id")
                or ""
            ),
            "candidate_stage": str(
                promotion.get("candidate_stage")
                or candidate_queue.get("candidate_stage")
                or active_candidate.get("stage")
                or council.get("maturity_stage")
                or ""
            ),
            "final_score": final_score,
            "execution_threshold": threshold,
            "true_blocker": true_blocker,
            "next_required": str(promotion.get("next_required") or "waiting for PG_EXECUTION_PACKET_V3").strip(),
            "selected_execution_lane": lane_context["selected_execution_lane"],
            "execution_lane": lane_context["execution_lane"],
            "lane_accepted": lane_context["lane_accepted"],
            "accepted_lanes": lane_context["accepted_lanes"],
            "current_candle_acceptance": lane_context["current_candle_acceptance"],
            "reason": "WAITING_FOR_EXECUTABLE_MODEL_COUNCIL_PACKET",
            "runtime_integrity": "WAITING_STUDY_PACKET",
            "model_council_wait": (
                f"packet={_v3_packet_id(study_packet) or study_packet.get('packet_id')} "
                f"state={execution_state} "
                f"side={side or 'HOLD'} "
                f"lane={lane_context['selected_execution_lane'] or 'NONE'} "
                f"lane_accepted={lane_context['lane_accepted']} "
                f"blocker={true_blocker}"
            ),
        }
    )
    return decision


def _v3_fail_decision(decision: Dict[str, Any], *, reason: str, runtime_integrity: Optional[str] = None) -> Dict[str, Any]:
    decision["will_click"] = False
    decision["reason"] = reason
    if runtime_integrity is not None:
        decision["runtime_integrity"] = runtime_integrity
    return decision


def _v3_packet_age_seconds(packet: Mapping[str, Any], now: float) -> Optional[float]:
    live_integrity = _v3_mapping(packet.get("live_integrity"))
    age_ms = _coerce_finite_float(live_integrity.get("packet_age_ms"))
    if age_ms is not None and age_ms >= 0.0:
        return float(age_ms) / 1000.0
    created = _coerce_finite_float(packet.get("created_epoch_sec") or packet.get("created_epoch"))
    if created is None:
        return None
    return max(0.0, float(now) - float(created))


def _v3_display_packet_ttl_seconds(packet: Mapping[str, Any]) -> Optional[float]:
    for key in ("ttl_sec", "time_to_live_sec", "valid_for_seconds", "freshness_window_sec", "max_signal_age_sec"):
        ttl = _coerce_finite_float(packet.get(key))
        if ttl is not None and ttl > 0.0:
            return float(ttl)
    return None


def _v3_display_packet_valid_until_epoch(packet: Mapping[str, Any]) -> Optional[float]:
    valid_until = _coerce_finite_float(packet.get("valid_until_epoch") or packet.get("valid_until_epoch_sec"))
    if valid_until is not None and valid_until > 0.0:
        return float(valid_until)
    packet_type = str(packet.get("packet_type") or "").strip().upper()
    schema_version = str(packet.get("schema_version") or "").strip()
    if packet_type == "STUDY_PACKET" or schema_version == PG_MODEL_COUNCIL_STUDY_SCHEMA_V3:
        created = _coerce_finite_float(packet.get("created_epoch") or packet.get("created_epoch_sec"))
        if created is not None and created > 0.0:
            ttl = _v3_display_packet_ttl_seconds(packet) or STUDY_PACKET_FALLBACK_TTL_SECONDS
            return float(created) + max(0.1, float(ttl))
    return None


def _v3_study_packet_is_current(
    packet: Mapping[str, Any],
    *,
    now: Optional[float] = None,
    max_packet_age_seconds: float = DEFAULT_MAX_SIGNAL_AGE_SECONDS,
) -> bool:
    timestamp = float(time.time() if now is None else now)
    packet_type = str(packet.get("packet_type") or "").strip().upper()
    schema_version = str(packet.get("schema_version") or "").strip()
    if packet_type != "STUDY_PACKET" and schema_version != PG_MODEL_COUNCIL_STUDY_SCHEMA_V3:
        return False
    if not str(packet.get("packet_id") or "").strip():
        return False
    valid_until = _v3_display_packet_valid_until_epoch(packet)
    if valid_until is None or valid_until <= timestamp:
        return False
    age = _v3_packet_age_seconds(packet, timestamp)
    packet_ttl = _v3_display_packet_ttl_seconds(packet) or 0.0
    max_age = max(0.05, float(max_packet_age_seconds), float(packet_ttl))
    return age is not None and age <= max_age


def _v3_runtime_integrity_check(
    packet: Optional[Mapping[str, Any]],
    *,
    expected_session_id: Optional[str] = None,
    expected_symbol: Optional[str] = None,
    expected_timeframe: Optional[str] = None,
    now: Optional[float] = None,
    max_packet_age_seconds: float = 2.0,
) -> Tuple[bool, str]:
    timestamp = float(time.time() if now is None else now)
    if not isinstance(packet, Mapping) or not packet:
        return False, "RUNTIME_INTEGRITY: PAYLOAD_MISSING"
    if str(packet.get("schema_version") or "").strip() != PG_EXECUTION_PACKET_SCHEMA_V3:
        return False, "RUNTIME_INTEGRITY: NON_V3_PACKET"
    if str(packet.get("packet_type") or "").strip() != PG_EXECUTION_PACKET_SCHEMA_V3:
        return False, "RUNTIME_INTEGRITY: PACKET_TYPE_NOT_EXECUTION"

    session_id = str(packet.get("session_id") or "").strip()
    symbol = str(packet.get("symbol") or "").strip()
    timeframe = str(packet.get("timeframe") or "").strip()
    if not session_id:
        return False, "RUNTIME_INTEGRITY: SESSION_ID_MISSING"
    if expected_session_id and session_id != str(expected_session_id).strip():
        return False, "RUNTIME_INTEGRITY: SESSION_ID_MISMATCH"
    if not symbol:
        return False, "RUNTIME_INTEGRITY: SYMBOL_MISSING"
    if expected_symbol and symbol != str(expected_symbol).strip():
        return False, "RUNTIME_INTEGRITY: SYMBOL_MISMATCH"
    if not timeframe:
        return False, "RUNTIME_INTEGRITY: TIMEFRAME_MISSING"
    if expected_timeframe and timeframe != str(expected_timeframe).strip():
        return False, "RUNTIME_INTEGRITY: TIMEFRAME_MISMATCH"

    for key in ("frame_id", "capture_count", "state_version"):
        parsed = _v3_packet_counter(packet, key)
        if parsed is None or parsed <= 0:
            return False, f"RUNTIME_INTEGRITY: {key.upper()}_MISSING_OR_INVALID"

    live_integrity = _v3_mapping(packet.get("live_integrity"))
    if _coerce_signal_bool(live_integrity.get("is_live")) is not True:
        return False, "RUNTIME_INTEGRITY: LIVE_FLAG_FALSE"
    for live_key in ("frame_advancing", "capture_advancing", "state_advancing"):
        if _coerce_signal_bool(live_integrity.get(live_key)) is not True:
            return False, f"RUNTIME_INTEGRITY: {live_key.upper()}_FALSE"
    if str(live_integrity.get("cache_status") or "").strip().lower() != "fresh":
        return False, "RUNTIME_INTEGRITY: CACHE_NOT_FRESH"
    if str(live_integrity.get("source") or "").strip().lower() not in {"model_council", "model-council"}:
        return False, "RUNTIME_INTEGRITY: SOURCE_NOT_MODEL_COUNCIL"
    if not _v3_live_hash(packet):
        return False, "RUNTIME_INTEGRITY: INPUT_FRAME_HASH_MISSING"

    valid_until = _coerce_finite_float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"))
    if valid_until is None or valid_until <= timestamp:
        return False, "RUNTIME_INTEGRITY: PACKET_EXPIRED"
    created = _coerce_finite_float(packet.get("created_epoch_sec") or packet.get("created_epoch"))
    if created is None or created <= 0.0:
        return False, "RUNTIME_INTEGRITY: CREATED_EPOCH_MISSING"
    age = _v3_packet_age_seconds(packet, timestamp)
    packet_ttl = _v3_display_packet_ttl_seconds(packet) or 0.0
    max_age = max(0.05, float(max_packet_age_seconds), float(packet_ttl))
    if age is None or age > max_age:
        return False, "RUNTIME_INTEGRITY: PACKET_STALE"

    return True, "RUNTIME_INTEGRITY: PASS"


def _v3_second_read_identity(packet: Mapping[str, Any], tracker_snapshot: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    identity = _v3_packet_identity(packet)
    live_identity = _v3_live_read_identity_from_tracker(tracker_snapshot)
    for key in ("frame_id", "capture_count", "state_version", "input_frame_hash"):
        value = live_identity.get(key)
        if value not in (None, ""):
            identity[key] = value
    return identity


def _v3_store_second_read_baseline(
    state: Dict[str, Any],
    packet: Mapping[str, Any],
    now: float,
    tracker_snapshot: Optional[Mapping[str, Any]] = None,
) -> None:
    baseline = _v3_second_read_identity(packet, tracker_snapshot)
    baseline["seen_at"] = float(now)
    state["v3_second_live_read_baseline"] = baseline


def _v3_counters_advanced(current: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    advanced = False
    for key in ("frame_id", "capture_count", "state_version"):
        current_value = current.get(key)
        previous_value = previous.get(key)
        if current_value is None or previous_value is None:
            return False
        try:
            cur = int(current_value)
            prev = int(previous_value)
        except Exception:
            return False
        if cur < prev:
            return False
        if cur > prev:
            advanced = True
    return advanced


def _v3_backend_live_read_confirmed(
    packet: Mapping[str, Any],
    tracker_snapshot: Optional[Mapping[str, Any]],
) -> bool:
    if not isinstance(tracker_snapshot, Mapping):
        return False
    live_integrity = _v3_mapping(packet.get("live_integrity"))
    if _coerce_signal_bool(live_integrity.get("is_live")) is not True:
        return False
    for live_key in ("frame_advancing", "capture_advancing", "state_advancing"):
        if _coerce_signal_bool(live_integrity.get(live_key)) is not True:
            return False
    if str(live_integrity.get("cache_status") or "").strip().lower() != "fresh":
        return False
    if str(live_integrity.get("source") or "").strip().lower() not in {"model_council", "model-council"}:
        return False
    packet_hash = _v3_live_hash(packet)
    tracker_identity = _v3_live_read_identity_from_tracker(tracker_snapshot)
    tracker_hash = str(tracker_identity.get("input_frame_hash") or "").strip()
    if not packet_hash or not tracker_hash or packet_hash != tracker_hash:
        return False
    for key in ("session_id", "symbol", "timeframe"):
        packet_value = str(packet.get(key) or "").strip()
        tracker_value = str(tracker_identity.get(key) or "").strip()
        if packet_value and tracker_value and packet_value != tracker_value:
            return False
    for key in ("frame_id", "capture_count", "state_version"):
        if _v3_packet_counter(tracker_identity, key) is None:
            return False
    return True


def _v3_backend_confirmed_packet_ready(packet: Mapping[str, Any], timestamp: float) -> bool:
    if not bool(packet.get("_backend_confirmed_execution_packet") or packet.get("_leased_execution_packet")):
        return False
    live_integrity = _v3_mapping(packet.get("live_integrity"))
    if _coerce_signal_bool(live_integrity.get("is_live")) is not True:
        return False
    for live_key in ("frame_advancing", "capture_advancing", "state_advancing"):
        if _coerce_signal_bool(live_integrity.get(live_key)) is not True:
            return False
    if str(live_integrity.get("cache_status") or "").strip().lower() != "fresh":
        return False
    if str(live_integrity.get("source") or "").strip().lower() not in {"model_council", "model-council"}:
        return False
    if not _v3_live_hash(packet):
        return False
    if _v3_packet_side(packet) not in {"BUY", "SELL"}:
        return False
    for key in ("frame_id", "capture_count", "state_version"):
        parsed = _v3_packet_counter(packet, key)
        if parsed is None or parsed <= 0:
            return False
    valid_until = _v3_display_packet_valid_until_epoch(packet)
    if valid_until is None or valid_until <= timestamp:
        return False
    return True


def _v3_gate1_second_live_read(
    state: Dict[str, Any],
    packet: Mapping[str, Any],
    now: Optional[float] = None,
    tracker_snapshot: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, str]:
    timestamp = float(time.time() if now is None else now)
    identity = _v3_second_read_identity(packet, tracker_snapshot)
    if _v3_backend_live_read_confirmed(packet, tracker_snapshot):
        identity["seen_at"] = timestamp
        state["v3_second_live_read_baseline"] = dict(identity)
        state["v3_second_live_read_confirmed"] = dict(identity)
        return True, "SECOND_READ_PASS"
    if _v3_backend_confirmed_packet_ready(packet, timestamp):
        identity["seen_at"] = timestamp
        identity["confirmation_source"] = str(packet.get("_backend_execution_packet_source") or "backend_confirmed_packet")
        state["v3_second_live_read_baseline"] = dict(identity)
        state["v3_second_live_read_confirmed"] = dict(identity)
        return True, "SECOND_READ_PASS"
    baseline = state.get("v3_second_live_read_baseline")
    if not isinstance(baseline, Mapping):
        _v3_store_second_read_baseline(state, packet, timestamp, tracker_snapshot)
        return False, "WAITING_SECOND_LIVE_READ"

    baseline_seen_at = _coerce_finite_float(baseline.get("seen_at"))
    if baseline_seen_at is None or (timestamp - float(baseline_seen_at)) > V3_SECOND_READ_BASELINE_MAX_AGE_SECONDS:
        _v3_store_second_read_baseline(state, packet, timestamp, tracker_snapshot)
        return False, "WAITING_SECOND_LIVE_READ_STALE_BASELINE_RESET"

    for key in ("session_id", "symbol", "timeframe"):
        if str(identity.get(key) or "") != str(baseline.get(key) or ""):
            _v3_store_second_read_baseline(state, packet, timestamp, tracker_snapshot)
            return False, f"WAITING_SECOND_LIVE_READ_CONTEXT_RESET_{key.upper()}"

    current_side = _normalize_trade_side(identity.get("side"))
    baseline_side = _normalize_trade_side(baseline.get("side"))
    if current_side not in {"BUY", "SELL"}:
        _v3_store_second_read_baseline(state, packet, timestamp, tracker_snapshot)
        return False, "WAITING_SECOND_LIVE_READ_NO_EXECUTION_SIDE"
    if baseline_side != current_side:
        _v3_store_second_read_baseline(state, packet, timestamp, tracker_snapshot)
        return False, "WAITING_SECOND_LIVE_READ_SIDE_CHANGED"

    if not _v3_counters_advanced(identity, baseline):
        return False, "WAITING_SECOND_LIVE_READ_LIVE_STATE_NOT_ADVANCING"

    state["v3_second_live_read_confirmed"] = dict(identity)
    return True, "SECOND_READ_PASS"


def _v3_gate2_trade_discipline(state: Mapping[str, Any], now: Optional[float] = None) -> Tuple[bool, str, int]:
    timestamp = float(time.time() if now is None else now)
    locked_until = float(state.get("v3_locked_until", state.get("locked_until", 0.0)) or 0.0)
    remaining = max(0, int(math.ceil(locked_until - timestamp)))
    if remaining > 0:
        return False, "TRADE_DISCIPLINE_LOCKED_5_TRADES_20_MINUTES", remaining
    active_until = float(state.get("v3_active_trade_until", 0.0) or 0.0)
    active_remaining = max(0, int(math.ceil(active_until - timestamp)))
    if active_remaining > 0:
        return False, "TRADE_DISCIPLINE_ACTIVE_TRADE_UNTIL_EXPIRY", active_remaining
    return True, "TRADE_DISCIPLINE_PASS", 0


def _v3_packet_has_dual_side_execution(packet: Mapping[str, Any]) -> bool:
    execution = _v3_mapping(packet.get("execution"))
    council = _v3_mapping(packet.get("model_council"))
    sides: set[str] = set()
    for key in ("sides", "executable_sides", "final_sides"):
        raw_sides = execution.get(key) if key in execution else council.get(key)
        if isinstance(raw_sides, Sequence) and not isinstance(raw_sides, (str, bytes, bytearray)):
            for raw in raw_sides:
                parsed = _normalize_trade_side(raw)
                if parsed in {"BUY", "SELL"}:
                    sides.add(parsed)

    buy_flag = _coerce_signal_bool(execution.get("buy_executable") or council.get("buy_executable"))
    sell_flag = _coerce_signal_bool(execution.get("sell_executable") or council.get("sell_executable"))
    if buy_flag and sell_flag:
        return True
    return "BUY" in sides and "SELL" in sides


def _v3_step_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _v3_time_sequence_field_is_time(field_text: str) -> bool:
    if not field_text:
        return True
    return any(token in field_text for token in ("time", "expiry", "expiration", "hour", "minute", "second"))


def _v3_validate_time_sequence(packet: Mapping[str, Any]) -> Tuple[bool, str]:
    execution = _v3_mapping(packet.get("execution"))
    time_sequence = _v3_mapping(execution.get("time_sequence"))
    if not time_sequence:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_MISSING"

    execution_expiry = _parse_expiry_seconds_value(execution.get("expiry_seconds"))
    target_seconds = _parse_expiry_seconds_value(time_sequence.get("target_seconds"))
    target_text = str(time_sequence.get("target_text") or "").strip()
    target_text_seconds = _parse_expiry_seconds_value(target_text)

    if target_seconds is None or int(target_seconds) <= 0:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_TARGET_SECONDS_MISSING"
    if not target_text:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_TARGET_TEXT_MISSING"
    if target_text_seconds is None or int(target_text_seconds) <= 0:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_TARGET_TEXT_INVALID"
    if int(target_text_seconds) != int(target_seconds):
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_TARGET_MISMATCH"
    if execution_expiry is None or int(execution_expiry) != int(target_seconds):
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_TARGET_MISMATCH"

    steps = time_sequence.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes, bytearray)) or len(steps) == 0:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_STEPS_MISSING"

    has_time_focus = False
    has_time_set = False
    has_time_confirm = False
    for raw_step in steps:
        if not isinstance(raw_step, Mapping):
            return False, "MODEL_COUNCIL_TIME_SEQUENCE_STEPS_INVALID"
        step = cast(Mapping[str, Any], raw_step)
        action = _v3_step_text(step.get("action"))
        field_text = _v3_step_text(
            step.get("field")
            or step.get("target")
            or step.get("control")
            or step.get("field_name")
            or step.get("selector")
        )
        if "amount" in action or "amount" in field_text:
            return False, "MODEL_COUNCIL_TIME_SEQUENCE_WRONG_FIELD"
        if field_text and not _v3_time_sequence_field_is_time(field_text):
            return False, "MODEL_COUNCIL_TIME_SEQUENCE_WRONG_FIELD"

        if action in {"focus_time_field", "open_time_picker", "select_existing_time"} or (
            action.startswith("focus") and _v3_time_sequence_field_is_time(field_text)
        ):
            has_time_focus = True
        if action in {
            "type_time",
            "set_time",
            "select_time",
            "select_time_preset",
            "adjust_time",
            "increment_time",
            "decrement_time",
        }:
            has_time_set = True
        if action == "type_time":
            value_seconds = _parse_expiry_seconds_value(step.get("value"))
            if value_seconds is None or int(value_seconds) != int(target_seconds):
                return False, "MODEL_COUNCIL_TIME_SEQUENCE_TARGET_MISMATCH"
        if action in {"confirm_time", "apply_time", "verify_time", "verify_time_if_possible"}:
            has_time_confirm = True

    if not has_time_focus:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_FOCUS_MISSING"
    if not has_time_set:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_TIME_SET_MISSING"
    if not has_time_confirm:
        return False, "MODEL_COUNCIL_TIME_SEQUENCE_CONFIRM_MISSING"
    return True, "MODEL_COUNCIL_TIME_SEQUENCE_VALID"


def _v3_gate3_model_council(packet: Mapping[str, Any]) -> Tuple[bool, str]:
    if _v3_packet_has_dual_side_execution(packet):
        return False, "MODEL_COUNCIL_CONFLICT_BOTH_SIDES"

    execution = _v3_mapping(packet.get("execution"))
    council = _v3_mapping(packet.get("model_council"))
    health = _v3_mapping(packet.get("runtime_model_health"))
    side = _normalize_trade_side(execution.get("side"))
    final_side = _normalize_trade_side(council.get("final_side"))
    state = str(execution.get("state") or "").strip().upper()
    final_state = str(council.get("final_state") or "").strip().upper()
    amount_action = str(execution.get("amount_action") or "DO_NOT_CHANGE_AMOUNT").strip().upper()

    if _coerce_signal_bool(execution.get("enabled")) is not True:
        return False, "MODEL_COUNCIL_NOT_EXECUTABLE"
    if state != "EXECUTABLE":
        return False, "MODEL_COUNCIL_NOT_EXECUTABLE"
    if final_state and final_state != "EXECUTABLE":
        return False, "MODEL_COUNCIL_FINAL_STATE_NOT_EXECUTABLE"
    if side not in {"BUY", "SELL"}:
        return False, "MODEL_COUNCIL_SIDE_MISSING"
    if final_side not in {"BUY", "SELL"}:
        return False, "MODEL_COUNCIL_FINAL_SIDE_MISSING"
    if side != final_side:
        return False, "MODEL_COUNCIL_SIDE_MISMATCH"
    if amount_action not in {"", "DO_NOT_CHANGE_AMOUNT", "PRESERVE", "PRESERVE_VISIBLE_AMOUNT"}:
        return False, "MODEL_COUNCIL_AMOUNT_CHANGE_FORBIDDEN"
    if _coerce_signal_bool(health.get("all_required_models_awake")) is not True:
        return False, "MODEL_COUNCIL_REQUIRED_MODELS_NOT_AWAKE"
    try:
        sequence_context = resolve_sequence_context(packet)
    except ValueError as exc:
        reason = str(exc).strip().upper() or "MODEL_COUNCIL_SEQUENCE_CONTEXT_MISSING"
        if "AMBIGUOUS" in reason:
            return False, "MODEL_COUNCIL_SEQUENCE_CONTEXT_AMBIGUOUS"
        return False, "MODEL_COUNCIL_SEQUENCE_CONTEXT_MISSING"
    if sequence_context.sequence_status != "COMPLETE":
        return False, f"MODEL_COUNCIL_PARTIAL_SEQUENCE_NOT_EXECUTABLE:{sequence_context.sequence_status}"
    if sequence_context.sequence_length < 50:
        return False, "MODEL_COUNCIL_SEQUENCE_LENGTH_BELOW_MINIMUM"
    entry_allowed, entry_reason = _entry_location_allows_trade(cast(Dict[str, Any], dict(packet)), side)
    if not entry_allowed:
        return False, f"MODEL_COUNCIL_ENTRY_LOCATION_BLOCKED:{entry_reason}"

    time_sequence_ok, time_sequence_reason = _v3_validate_time_sequence(packet)
    if not time_sequence_ok:
        return False, time_sequence_reason
    if _v3_packet_expiry_seconds(packet) <= 0:
        return False, "MODEL_COUNCIL_EXPIRY_MISSING"
    return True, "MODEL_COUNCIL_EXECUTABLE"


def _v3_box_has_point(boxes: Mapping[str, Any], key: str) -> bool:
    value = boxes.get(key)
    if not isinstance(value, Mapping):
        return False
    return _coerce_finite_float(value.get("x")) is not None and _coerce_finite_float(value.get("y")) is not None


def _v3_box_has_any_point(boxes: Mapping[str, Any], *keys: str) -> bool:
    return any(_v3_box_has_point(boxes, key) for key in keys)


def _v3_calibration_layout_check(boxes: Mapping[str, Any]) -> Tuple[bool, str]:
    rel_points: Dict[str, Tuple[float, float]] = {}
    for key, value in boxes.items():
        if key == "capabilities" or not isinstance(value, Mapping):
            continue
        rel_x = _coerce_finite_float(value.get("x"))
        rel_y = _coerce_finite_float(value.get("y"))
        if rel_x is not None and rel_y is not None:
            rel_points[str(key)] = (float(rel_x), float(rel_y))
    return _calibration_layout_reason(rel_points)


def _v3_calibration_check(boxes: Mapping[str, Any], packet: Mapping[str, Any]) -> Tuple[bool, str]:
    if any("amount" in str(key).strip().lower() for key in boxes.keys()):
        return False, "CALIBRATION_AMOUNT_CONTROL_FORBIDDEN"
    if not _v3_box_has_any_point(boxes, "buy_icon", "buy_button"):
        return False, "CALIBRATION_MISSING_BUY_CONTROL"
    if not _v3_box_has_any_point(boxes, "sell_icon", "sell_button"):
        return False, "CALIBRATION_MISSING_SELL_CONTROL"
    if not (
        _v3_box_has_any_point(boxes, "time_input", "expiry_time_field", "time_button", "time_box")
    ):
        return False, "CALIBRATION_MISSING_TIME_CONTROL"
    layout_ok, layout_reason = _v3_calibration_layout_check(boxes)
    if not layout_ok:
        return False, layout_reason

    expiry = _v3_packet_expiry_seconds(packet)
    exact_keys = (f"time_{expiry}", f"time_preset_{expiry}")
    has_exact_preset = any(_v3_box_has_point(boxes, key) for key in exact_keys)
    has_combined_time_input = (
        _v3_box_has_any_point(boxes, "time_input", "time_box", "expiry_time_field", "time_button")
    )
    has_split_typed_time = (
        _v3_box_has_any_point(boxes, "hourly_input", "hour_input", "hours_input")
        and _v3_box_has_any_point(boxes, "minute_input", "minutely_input", "minutes_input")
        and _v3_box_has_any_point(boxes, "second_input", "seconds_input", "second_field", "seconds_field")
    )
    has_typed_time = has_combined_time_input or has_split_typed_time
    has_stepper_time = (
        _v3_box_has_any_point(boxes, "hourly_minus", "hour_minus", "hours_minus", "expiry_minus")
        and _v3_box_has_any_point(boxes, "hourly_plus", "hour_plus", "hours_plus", "expiry_plus")
        and _v3_box_has_any_point(boxes, "minute_minus", "minutely_minus", "minutes_minus")
        and _v3_box_has_any_point(boxes, "minute_plus", "minutely_plus", "minutes_plus")
    )
    if not (has_exact_preset or has_typed_time or has_stepper_time):
        return False, "CALIBRATION_MISSING_TIME_SEQUENCE_CONTROLS"

    execution = _v3_mapping(packet.get("execution"))
    time_sequence = _v3_mapping(execution.get("time_sequence"))
    steps = time_sequence.get("steps")
    if isinstance(steps, Sequence) and not isinstance(steps, (str, bytes, bytearray)):
        actions = {_v3_step_text(cast(Mapping[str, Any], step).get("action")) for step in steps if isinstance(step, Mapping)}
        if "type_time" in actions and not has_typed_time:
            return False, "CALIBRATION_MISSING_TIME_FIELD_INPUTS"
        if any(action.startswith("verify_time") for action in actions) and not (
            _v3_box_has_any_point(boxes, "time_input", "expiry_time_field", "time_box", "time_button")
        ):
            return False, "CALIBRATION_MISSING_TIME_VERIFICATION_REGION"
    return True, "CALIBRATION_VALID"


def _v3_already_executed(state: Mapping[str, Any], packet: Mapping[str, Any]) -> bool:
    key = _v3_packet_execution_key(packet)
    executed = state.get("v3_executed_packet_keys")
    if not isinstance(executed, Sequence) or isinstance(executed, (str, bytes, bytearray)):
        return False
    return key in {str(item) for item in executed[-V3_EXECUTED_PACKET_LIMIT:]}


def _v3_record_execution(state: Dict[str, Any], packet: Mapping[str, Any], now: Optional[float] = None) -> Tuple[int, int]:
    timestamp = float(time.time() if now is None else now)
    executed = state.get("v3_executed_packet_keys")
    if not isinstance(executed, list):
        executed = []
    key = _v3_packet_execution_key(packet)
    if key not in {str(item) for item in executed[-V3_EXECUTED_PACKET_LIMIT:]}:
        executed.append(key)
    state["v3_executed_packet_keys"] = executed[-V3_EXECUTED_PACKET_LIMIT:]
    expiry_seconds = max(0, int(_v3_packet_expiry_seconds(packet)))
    if expiry_seconds > 0:
        active_until = timestamp + float(expiry_seconds)
        state["v3_active_trade_until"] = max(float(state.get("v3_active_trade_until", 0.0) or 0.0), active_until)
        state["v3_active_trade_expiry_seconds"] = expiry_seconds
        state["v3_active_trade_packet_id"] = _v3_packet_id(packet)

    count = int(state.get("v3_trade_count", 0) or 0) + 1
    locked_for = 0
    if count >= THREE_GATE_TRADE_LIMIT:
        locked_for = THREE_GATE_LOCK_SECONDS
        state["v3_locked_until"] = timestamp + float(locked_for)
        state["v3_trade_count"] = 0
        state.pop("v3_second_live_read_baseline", None)
        state.pop("v3_second_live_read_confirmed", None)
    else:
        state["v3_trade_count"] = count
    _three_gate_save_state(state)
    return int(state.get("v3_trade_count", 0) or 0), locked_for


def _v3_mark_packet_consumed(state: Dict[str, Any], packet: Mapping[str, Any]) -> None:
    executed = state.get("v3_executed_packet_keys")
    if not isinstance(executed, list):
        executed = []
    key = _v3_packet_execution_key(packet)
    if key not in {str(item) for item in executed[-V3_EXECUTED_PACKET_LIMIT:]}:
        executed.append(key)
    state["v3_executed_packet_keys"] = executed[-V3_EXECUTED_PACKET_LIMIT:]
    _three_gate_save_state(state)


def _v3_window_bounds(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    rect = get_window_rect(hwnd)
    if rect is None:
        return None
    return rect_bounds(rect)


def _v3_coordinate_report(hwnd: int, boxes: Mapping[str, Any], packet: Mapping[str, Any]) -> Dict[str, Any]:
    bounds = _v3_window_bounds(hwnd)
    if bounds is None:
        return {"ok": False, "errors": ["WINDOW_RECT_MISSING"], "points": {}}
    return shooter_modes.build_coordinate_report(
        boxes,
        bounds,
        side=_v3_packet_side(packet) or "",
        expiry_seconds=max(0, int(_v3_packet_expiry_seconds(packet))),
    )


def _v3_apply_shooter_mode(
    hwnd: int,
    boxes: Mapping[str, Any],
    packet: Mapping[str, Any],
    decision: Mapping[str, Any],
    state: Dict[str, Any],
    mode: Any = None,
    *,
    now: Optional[float] = None,
    action_options: Optional[Mapping[str, Any]] = None,
) -> shooter_modes.ShooterModeResult:
    resolved_mode = shooter_modes.resolve_shooter_mode(mode)
    timestamp = float(time.time() if now is None else now)
    options = dict(action_options or {})

    if resolved_mode == shooter_modes.ShooterMode.STUDY_ONLY:
        LOGGER.info("STUDY_ONLY: observed executable V3 packet_id=%s without broker action", _v3_packet_id(packet))
        return shooter_modes.ShooterModeResult(resolved_mode, False, False, "STUDY_ONLY_OBSERVED")

    if resolved_mode == shooter_modes.ShooterMode.PAPER_EXECUTION:
        result = shooter_modes.record_paper_execution(packet, decision, now=timestamp)
        _v3_mark_packet_consumed(state, packet)
        LOGGER.info("PAPER_EXECUTION: recorded packet_id=%s at %s", _v3_packet_id(packet), result.record_path)
        return result

    if resolved_mode == shooter_modes.ShooterMode.DRY_RUN_CLICK:
        report = _v3_coordinate_report(hwnd, boxes, packet)
        if not bool(report.get("ok")):
            return shooter_modes.ShooterModeResult(resolved_mode, False, False, "DRY_RUN_COORDINATE_VALIDATION_FAILED")
        result = shooter_modes.record_dry_run_click(packet, decision, report, now=timestamp)
        _v3_mark_packet_consumed(state, packet)
        LOGGER.info("DRY_RUN_CLICK: recorded click plan for packet_id=%s at %s", _v3_packet_id(packet), result.record_path)
        return result

    if resolved_mode == shooter_modes.ShooterMode.CALIBRATION_TEST:
        report = _v3_coordinate_report(hwnd, boxes, packet)
        if not bool(report.get("ok")):
            return shooter_modes.ShooterModeResult(resolved_mode, False, False, "CALIBRATION_TEST_COORDINATE_VALIDATION_FAILED")
        show_box_preview(hwnd, cast(Dict[str, Dict[str, Any]], dict(boxes)))
        result = shooter_modes.record_calibration_test(packet, decision, report, now=timestamp)
        _v3_mark_packet_consumed(state, packet)
        LOGGER.info("CALIBRATION_TEST: highlighted and recorded packet_id=%s at %s", _v3_packet_id(packet), result.record_path)
        return result

    if resolved_mode in {shooter_modes.ShooterMode.LIVE_READY, shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION}:
        bounds = _v3_window_bounds(hwnd)
        if bounds is None:
            return shooter_modes.ShooterModeResult(resolved_mode, False, False, f"{resolved_mode.value}_WINDOW_RECT_MISSING")
        max_packet_age_seconds = float(options.get("max_packet_age_seconds") or DEFAULT_MAX_SIGNAL_AGE_SECONDS)
        constitution = evaluate_execution_constitution(
            packet,
            decision,
            now_epoch=timestamp,
            first_read_confirmed=str(decision.get("gate_1_second_read") or "").upper() == "PASS",
            max_packet_age_seconds=max_packet_age_seconds,
        )
        if not constitution.ok:
            reason = f"{resolved_mode.value}_CONSTITUTION_BLOCKED:{constitution.reason}"
            if resolved_mode == shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION:
                return shooter_modes.record_live_behavior_validation(
                    packet,
                    decision,
                    clicked=False,
                    reason=reason,
                    action_report={"constitution": constitution.as_dict()},
                    now=timestamp,
                )
            return shooter_modes.record_live_ready(
                packet,
                decision,
                clicked=False,
                reason=reason,
                rehearsal={"constitution": constitution.as_dict()},
                now=timestamp,
            )
        rehearsal = rehearse_execution(
            packet,
            decision,
            boxes,
            bounds,
            latest_packet=packet,
            now_epoch=timestamp,
            require_broker_click_safe=True,
            max_packet_age_seconds=max_packet_age_seconds,
        )
        if not bool(rehearsal.get("ready")):
            reason = f"{resolved_mode.value}_REHEARSAL_BLOCKED:{rehearsal.get('reason')}"
            if resolved_mode == shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION:
                return shooter_modes.record_live_behavior_validation(
                    packet,
                    decision,
                    clicked=False,
                    reason=reason,
                    action_report=rehearsal,
                    now=timestamp,
                )
            return shooter_modes.record_live_ready(
                packet,
                decision,
                clicked=False,
                reason=reason,
                rehearsal=rehearsal,
                now=timestamp,
            )
        if not _v3_live_broker_clicks_explicitly_enabled():
            reason = f"{resolved_mode.value}_ENV_NOT_ARMED:{LIVE_BROKER_CLICK_ENV}"
            if resolved_mode == shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION:
                return shooter_modes.record_live_behavior_validation(
                    packet,
                    decision,
                    clicked=False,
                    reason=reason,
                    action_report=rehearsal,
                    now=timestamp,
                )
            return shooter_modes.record_live_ready(
                packet,
                decision,
                clicked=False,
                reason=reason,
                rehearsal=rehearsal,
                now=timestamp,
            )
        kwargs: Dict[str, Any] = {"allow_live_clicks": True}
        if options:
            kwargs.update(
                {
                    "broker_timing_profile_path": options.get("broker_timing_profile_path"),
                    "action_speed": str(options.get("action_speed") or "balanced"),
                    "record_action_evidence": bool(options.get("record_action_evidence", False)),
                    "live_behavior_validation": resolved_mode == shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION,
                    "session_id": str(options.get("session_id") or ""),
                    "time_button_wait_override_ms": options.get("time_button_wait_override_ms"),
                    "action_status_callback": options.get("action_status_callback"),
                }
            )
        clicked = execute_v3_packet_trade(hwnd, cast(Dict[str, Dict[str, Any]], dict(boxes)), packet, **kwargs)
        action_report = _last_action_sequence_result.as_dict() if _last_action_sequence_result is not None else rehearsal
        reason = f"{resolved_mode.value}_CLICK_SENT" if clicked else f"{resolved_mode.value}_CLICK_FAILED"
        if resolved_mode == shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION:
            result = shooter_modes.record_live_behavior_validation(
                packet,
                decision,
                clicked=clicked,
                reason=reason,
                action_report=action_report,
                now=timestamp,
            )
        else:
            rehearsal_with_action = dict(rehearsal)
            rehearsal_with_action["action_sequence"] = action_report
            result = shooter_modes.record_live_ready(
                packet,
                decision,
                clicked=clicked,
                reason=reason,
                rehearsal=rehearsal_with_action,
                now=timestamp,
            )
        if clicked:
            _v3_record_execution(state, packet, now=timestamp)
        return result

    result = shooter_modes.record_live_disabled(packet, decision, now=timestamp)
    _v3_mark_packet_consumed(state, packet)
    LOGGER.warning("LIVE_DISABLED: broker click blocked for packet_id=%s; record=%s", _v3_packet_id(packet), result.record_path)
    return result


def _evaluate_v3_shooter_decision(
    packet: Optional[Dict[str, Any]],
    state: Dict[str, Any],
    boxes: Mapping[str, Any],
    *,
    tracker_snapshot: Optional[Mapping[str, Any]] = None,
    expected_session_id: Optional[str] = None,
    expected_symbol: Optional[str] = None,
    expected_timeframe: Optional[str] = None,
    now: Optional[float] = None,
    max_packet_age_seconds: float = 2.0,
) -> Dict[str, Any]:
    timestamp = float(time.time() if now is None else now)
    decision = _v3_base_decision(packet, timestamp)

    runtime_ok, runtime_reason = _v3_runtime_integrity_check(
        packet,
        expected_session_id=expected_session_id,
        expected_symbol=expected_symbol,
        expected_timeframe=expected_timeframe,
        now=timestamp,
        max_packet_age_seconds=max_packet_age_seconds,
    )
    decision["runtime_integrity"] = "PASS" if runtime_ok else "RUNTIME_INTEGRITY"
    if not runtime_ok or not isinstance(packet, Mapping):
        return _v3_fail_decision(decision, reason=runtime_reason)

    side = _v3_packet_side(packet)
    expiry = _v3_packet_expiry_seconds(packet)
    decision["side"] = side
    if expiry > 0:
        decision["expiry_seconds"] = expiry

    if _v3_already_executed(state, packet):
        decision["gate_1_second_read"] = "BLOCKED_DUPLICATE"
        return _v3_fail_decision(decision, reason="DUPLICATE_PACKET_NOT_REFIRED")

    gate1_ok, gate1_reason = _v3_gate1_second_live_read(state, packet, timestamp, tracker_snapshot)
    decision["gate_1_second_read"] = "PASS" if gate1_ok else "WAIT"
    if not gate1_ok:
        return _v3_fail_decision(decision, reason=gate1_reason)

    gate2_ok, gate2_reason, gate2_remaining = _v3_gate2_trade_discipline(state, timestamp)
    decision["gate_2_trade_discipline"] = "PASS" if gate2_ok else "LOCKED"
    if gate2_remaining > 0:
        decision["discipline_remaining_seconds"] = gate2_remaining
    if not gate2_ok:
        return _v3_fail_decision(decision, reason=gate2_reason)

    gate3_ok, gate3_reason = _v3_gate3_model_council(packet)
    decision["gate_3_model_council"] = "PASS" if gate3_ok else "FAIL"
    if not gate3_ok:
        return _v3_fail_decision(decision, reason=gate3_reason)

    packet_validation = validate_execution_packet_v3(
        packet,
        expected_session_id=expected_session_id,
        expected_symbol=expected_symbol,
        expected_timeframe=expected_timeframe,
        now_epoch=timestamp,
        require_executable=True,
        require_broker_click_safe_identity=False,
    )
    decision["packet_validation"] = "PASS" if packet_validation.ok else "FAIL"
    if not packet_validation.ok:
        runtime_integrity = "RUNTIME_INTEGRITY" if "RUNTIME_INTEGRITY" in packet_validation.categories else None
        return _v3_fail_decision(
            decision,
            reason=f"PACKET_VALIDATION:{packet_validation.first_reason}",
            runtime_integrity=runtime_integrity,
        )

    calibration_ok, calibration_reason = _v3_calibration_check(boxes, packet)
    decision["calibration"] = "VALID" if calibration_ok else "INVALID"
    if not calibration_ok:
        return _v3_fail_decision(decision, reason=calibration_reason)

    decision["will_click"] = True
    decision["reason"] = "SECOND_READ_PASS; DISCIPLINE_PASS; MODEL_COUNCIL_EXECUTABLE; CALIBRATION_VALID"
    return decision


def _v3_pre_click_confirmation(
    packet: Mapping[str, Any],
    latest_packet: Optional[Mapping[str, Any]],
    *,
    expected_session_id: Optional[str] = None,
    now: Optional[float] = None,
    max_packet_age_seconds: float = 2.0,
) -> Tuple[bool, str]:
    timestamp = float(time.time() if now is None else now)
    if not isinstance(latest_packet, Mapping):
        return False, "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"

    runtime_ok, runtime_reason = _v3_runtime_integrity_check(
        latest_packet,
        expected_session_id=expected_session_id,
        now=timestamp,
        max_packet_age_seconds=max_packet_age_seconds,
    )
    if not runtime_ok:
        return False, runtime_reason.replace("RUNTIME_INTEGRITY: ", "PRE_CLICK_")

    for key in ("session_id", "symbol", "timeframe"):
        if str(packet.get(key) or "").strip() != str(latest_packet.get(key) or "").strip():
            return False, "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"

    if _v3_packet_execution_key(packet) != _v3_packet_execution_key(latest_packet):
        return False, "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"

    if _v3_packet_expiry_seconds(packet) != _v3_packet_expiry_seconds(latest_packet):
        return False, "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"

    def time_contract(candidate: Mapping[str, Any]) -> tuple[int, str]:
        execution = _v3_mapping(candidate.get("execution"))
        time_sequence = _v3_mapping(execution.get("time_sequence"))
        target_seconds = _parse_expiry_seconds_value(time_sequence.get("target_seconds"))
        target_text = str(time_sequence.get("target_text") or "").strip()
        return int(target_seconds or 0), target_text

    if time_contract(packet) != time_contract(latest_packet):
        return False, "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"

    side = _v3_packet_side(packet)
    latest_side = _v3_packet_side(latest_packet)
    latest_council = _v3_mapping(latest_packet.get("model_council"))
    latest_final_side = _normalize_trade_side(latest_council.get("final_side"))
    if side not in {"BUY", "SELL"} or latest_side != side or latest_final_side != side:
        return False, "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"

    gate3_ok, gate3_reason = _v3_gate3_model_council(latest_packet)
    if not gate3_ok:
        return False, f"PRE_CLICK_{gate3_reason}"

    packet_viewport = str(packet.get("viewport_hash") or "").strip()
    latest_viewport = str(latest_packet.get("viewport_hash") or "").strip()
    if packet_viewport and latest_viewport and packet_viewport != latest_viewport:
        return False, "PRE_CLICK_PACKET_STALE_OR_MISMATCHED"

    return True, "PRE_CLICK_CONFIRMATION_PASS"


def _v3_log_final_decision(decision: Mapping[str, Any]) -> None:
    try:
        LOGGER.debug("V3_SHOOTER_DECISION %s", json.dumps(dict(decision), sort_keys=True, default=str))
    except Exception:
        LOGGER.debug("V3_SHOOTER_DECISION %s", decision)


def _extract_model_council_packet(payload: Dict[str, Any], *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    timestamp = float(now if now is not None else time.time())

    def current_execution_packet(candidate: Mapping[str, Any], source: str) -> Optional[Dict[str, Any]]:
        if str(candidate.get("schema_version") or "").strip() != PG_EXECUTION_PACKET_SCHEMA_V3:
            return None
        if str(candidate.get("packet_type") or "").strip() != PG_EXECUTION_PACKET_SCHEMA_V3:
            return None
        valid_until = _coerce_finite_float(candidate.get("valid_until_epoch_sec") or candidate.get("valid_until_epoch"))
        if valid_until is None or valid_until <= timestamp:
            LOGGER.debug(
                "Ignoring stale V3 execution packet from %s: packet_id=%s valid_until=%s now=%s",
                source,
                candidate.get("packet_id"),
                valid_until,
                timestamp,
            )
            return None
        validation = validate_execution_packet_v3(
            candidate,
            now_epoch=timestamp,
            require_executable=True,
        )
        if not validation.ok or validation.side not in {"BUY", "SELL"} or validation.expiry_seconds is None:
            LOGGER.debug(
                "Ignoring non-executable V3 packet from %s: packet_id=%s reason=%s",
                source,
                candidate.get("packet_id"),
                validation.first_reason,
            )
            return None
        packet = dict(candidate)
        packet["_backend_confirmed_execution_packet"] = True
        packet["_backend_execution_packet_source"] = source
        return cast(Dict[str, Any], packet)

    packet = current_execution_packet(payload, "root")
    if packet is not None:
        return packet
    for key in (
        "execution_packet",
        "model_council_packet",
        "latest_execution_packet",
        "latest_model_council_packet",
        "latest_packet",
        "packet",
    ):
        nested = payload.get(key)
        if isinstance(nested, dict):
            packet = current_execution_packet(nested, key)
            if packet is not None:
                return packet

    # Runtime trace wraps endpoint payloads several layers deep. The shooter
    # still accepts only a validated PG_EXECUTION_PACKET_V3, but this prevents a
    # short execution/latest timeout from making the calibrated click path miss
    # an already-promoted packet.
    priority_keys = (
        "payload",
        "execution_packet",
        "model_council_packet",
        "latest_execution_packet",
        "latest_model_council_packet",
        "latest_packet",
        "packet",
        "execution_latest",
        "endpoints",
        "dataflow",
        "trace",
    )
    seen: set[int] = set()

    def scan_nested(value: Any, source: str, depth: int = 0) -> Optional[Dict[str, Any]]:
        if depth > 8:
            return None
        if isinstance(value, Mapping):
            marker = id(value)
            if marker in seen:
                return None
            seen.add(marker)
            packet_candidate = current_execution_packet(value, source)
            if packet_candidate is not None:
                return packet_candidate
            for child_key in priority_keys:
                child = value.get(child_key)
                if isinstance(child, (Mapping, list)):
                    packet_candidate = scan_nested(child, f"{source}.{child_key}", depth + 1)
                    if packet_candidate is not None:
                        return packet_candidate
            for child_key, child in value.items():
                if child_key in priority_keys:
                    continue
                if isinstance(child, (Mapping, list)):
                    packet_candidate = scan_nested(child, f"{source}.{child_key}", depth + 1)
                    if packet_candidate is not None:
                        return packet_candidate
        elif isinstance(value, list):
            for idx, child in enumerate(value[:32]):
                if isinstance(child, (Mapping, list)):
                    packet_candidate = scan_nested(child, f"{source}[{idx}]", depth + 1)
                    if packet_candidate is not None:
                        return packet_candidate
        return None

    return scan_nested(payload, "runtime_trace")


def _first_visible_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _synthesize_model_council_study_packet(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    result = _v3_mapping(payload.get("model_council_result"))
    source = result if result else dict(payload)
    council = _v3_mapping(source.get("model_council") or payload.get("model_council"))
    promotion = _v3_mapping(
        source.get("promotion_trace")
        or payload.get("promotion_trace")
        or council.get("promotion_trace")
    )
    execution = _v3_mapping(source.get("execution") or payload.get("execution"))
    candidate_queue = _v3_mapping(
        source.get("trade_candidate_queue")
        or payload.get("trade_candidate_queue")
        or council.get("trade_candidate_queue")
    )
    active_candidate = _v3_mapping(candidate_queue.get("active_candidate"))
    if not any((council, promotion, execution, candidate_queue)):
        return None

    state = str(
        _first_visible_value(
            execution.get("state"),
            council.get("final_state"),
            promotion.get("promotion_result"),
            source.get("state"),
            payload.get("state"),
            "WATCHING",
        )
    ).strip().upper()
    raw_state = state
    missing_execution_authority = raw_state in {
        "EXECUTABLE",
        "EXECUTABLE_PACKET",
        "EXECUTION_READY",
        "READY_TO_EXECUTE",
    }
    if missing_execution_authority:
        state = "WATCHING"
    side = (
        _normalize_trade_side(execution.get("side"))
        or _normalize_trade_side(council.get("final_side"))
        or _normalize_trade_side(promotion.get("candidate_side"))
        or _normalize_trade_side(candidate_queue.get("candidate_side"))
        or _normalize_trade_side(active_candidate.get("side"))
    )
    packet_id = str(
        _first_visible_value(
            source.get("packet_id"),
            payload.get("packet_id"),
            promotion.get("packet_id"),
            council.get("packet_id"),
        )
        or ""
    ).strip()
    if not packet_id:
        seed = "|".join(
            str(_first_visible_value(value, ""))
            for value in (
                payload.get("session_id"),
                source.get("session_id"),
                payload.get("state_version"),
                source.get("state_version"),
                payload.get("decision_version"),
                source.get("decision_version"),
                side,
                state,
                promotion.get("candidate_id"),
                council.get("decision_id"),
            )
        )
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        packet_id = f"study_{digest}"

    true_blocker = str(
        _first_visible_value(
            source.get("true_blocker"),
            payload.get("true_blocker"),
            promotion.get("true_blocker"),
            promotion.get("blocked_by"),
            source.get("block_reason"),
            payload.get("block_reason"),
            council.get("true_blocker"),
            council.get("arbitration_reason"),
            "EXECUTION_PACKET_NOT_PUBLISHED",
        )
    ).strip()
    if missing_execution_authority:
        true_blocker = "EXECUTION_PACKET_NOT_PUBLISHED"
    created_epoch = _coerce_finite_float(
        _first_visible_value(
            source.get("created_epoch"),
            payload.get("created_epoch"),
            source.get("last_capture_epoch"),
            payload.get("last_capture_epoch"),
            source.get("last_capture_started_epoch"),
            payload.get("last_capture_started_epoch"),
            payload.get("timestamp_epoch"),
            payload.get("timestamp"),
            time.time(),
        )
    )
    if created_epoch is None:
        created_epoch = time.time()
    valid_until_epoch = _coerce_finite_float(
        _first_visible_value(
            source.get("valid_until_epoch"),
            source.get("valid_until_epoch_sec"),
            payload.get("valid_until_epoch"),
            payload.get("valid_until_epoch_sec"),
            source.get("decision_valid_until_epoch"),
            payload.get("decision_valid_until_epoch"),
        )
    )
    if valid_until_epoch is None or valid_until_epoch <= created_epoch:
        valid_until_epoch = created_epoch + STUDY_PACKET_FALLBACK_TTL_SECONDS

    execution_payload = dict(execution)
    execution_payload["enabled"] = False
    execution_payload["state"] = state or "WATCHING"
    execution_payload["side"] = side

    council_payload = dict(council)
    if missing_execution_authority:
        council_payload["final_state"] = state or "WATCHING"
    else:
        council_payload.setdefault("final_state", state or "WATCHING")
    council_payload.setdefault("final_side", side)
    council_payload.setdefault(
        "final_execution_score",
        _first_visible_value(source.get("final_execution_score"), promotion.get("final_execution_score")),
    )
    council_payload.setdefault(
        "execution_threshold",
        _first_visible_value(source.get("execution_threshold"), promotion.get("execution_threshold")),
    )
    council_payload.setdefault("true_blocker", true_blocker)

    promotion_payload = dict(promotion)
    promotion_payload.setdefault("packet_id", packet_id)
    promotion_payload.setdefault("candidate_side", side or "HOLD")
    if missing_execution_authority:
        promotion_payload["true_blocker"] = true_blocker
        promotion_payload["blocked_by"] = true_blocker
        promotion_payload["promotion_result"] = state or "WATCHING"
        promotion_payload["packet_result"] = "STUDY_PACKET_SYNTHESIZED_EXECUTION_AUTHORITY_MISSING"
        promotion_payload["next_required"] = "publish fresh PG_EXECUTION_PACKET_V3 before shooter gates can open"
    else:
        promotion_payload.setdefault("true_blocker", true_blocker)
        promotion_payload.setdefault("blocked_by", true_blocker)
        promotion_payload.setdefault("promotion_result", state or "WATCHING")
        promotion_payload.setdefault("packet_result", "STUDY_PACKET_SYNTHESIZED")
        promotion_payload.setdefault("next_required", "publish PG_EXECUTION_PACKET_V3 when executable")
    lane_context = _v3_execution_lane_context(source, council_payload, promotion_payload)
    if lane_context["selected_execution_lane"]:
        promotion_payload.setdefault("selected_lane", lane_context["selected_execution_lane"])
    if lane_context["lane_accepted"] is not None:
        promotion_payload.setdefault("lane_accepted", lane_context["lane_accepted"])
    if lane_context["accepted_lanes"]:
        promotion_payload.setdefault("accepted_lanes", lane_context["accepted_lanes"])
    if lane_context["current_candle_acceptance"]:
        promotion_payload.setdefault("current_candle_acceptance", lane_context["current_candle_acceptance"])

    return {
        "schema_version": PG_MODEL_COUNCIL_STUDY_SCHEMA_V3,
        "packet_id": packet_id,
        "packet_type": "STUDY_PACKET",
        "session_id": str(_first_visible_value(source.get("session_id"), payload.get("session_id"), "") or ""),
        "symbol": str(_first_visible_value(source.get("symbol"), payload.get("symbol"), "") or ""),
        "timeframe": str(_first_visible_value(source.get("timeframe"), payload.get("timeframe"), "") or ""),
        "frame_id": _first_visible_value(source.get("frame_id"), payload.get("frame_id")),
        "capture_count": _first_visible_value(source.get("capture_count"), payload.get("capture_count")),
        "state_version": _first_visible_value(source.get("state_version"), payload.get("state_version"), payload.get("decision_version")),
        "created_epoch": created_epoch,
        "valid_until_epoch": valid_until_epoch,
        "ttl_sec": max(0.1, float(valid_until_epoch) - float(created_epoch)),
        "execution": execution_payload,
        "model_council": council_payload,
        "promotion_trace": promotion_payload,
        "execution_lane": lane_context["execution_lane"],
        "selected_execution_lane": lane_context["selected_execution_lane"],
        "current_candle_acceptance": lane_context["current_candle_acceptance"],
        "trade_candidate_queue": candidate_queue,
        "council_scores": _v3_mapping(source.get("council_scores") or council.get("council_scores")),
        "reality_adjustments": _v3_mapping(source.get("reality_adjustments") or council.get("reality_adjustments")),
        "final_execution_score": _first_visible_value(
            source.get("final_execution_score"),
            council_payload.get("final_execution_score"),
            promotion_payload.get("final_execution_score"),
        ),
        "execution_threshold": _first_visible_value(
            source.get("execution_threshold"),
            council_payload.get("execution_threshold"),
            promotion_payload.get("execution_threshold"),
        ),
        "reason": str(_first_visible_value(council_payload.get("arbitration_reason"), true_blocker) or ""),
        "true_blocker": true_blocker,
        "synthetic_source": "model_council_result_compat",
    }


def _extract_model_council_study_packet(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(payload.get("packet_type") or "").strip().upper() == "STUDY_PACKET":
        return payload
    if str(payload.get("schema_version") or "").strip() == PG_MODEL_COUNCIL_STUDY_SCHEMA_V3:
        return payload
    for key in (
        "model_council_study_packet",
        "study_packet",
        "latest_model_council_study_packet",
        "latest_study_packet",
    ):
        nested = payload.get(key)
        if isinstance(nested, dict):
            packet_type = str(nested.get("packet_type") or "").strip().upper()
            schema_version = str(nested.get("schema_version") or "").strip()
            if packet_type == "STUDY_PACKET" or schema_version == PG_MODEL_COUNCIL_STUDY_SCHEMA_V3:
                return cast(Dict[str, Any], nested)
    result = payload.get("model_council_result")
    if isinstance(result, dict):
        packet = _extract_model_council_study_packet(cast(Dict[str, Any], result))
        if isinstance(packet, dict):
            return packet
    synthesized = _synthesize_model_council_study_packet(payload)
    if isinstance(synthesized, dict):
        return synthesized
    if isinstance(result, dict):
        synthesized = _synthesize_model_council_study_packet(cast(Dict[str, Any], result))
        if isinstance(synthesized, dict):
            return synthesized
    latest_signal = payload.get("latest_signal")
    if isinstance(latest_signal, dict):
        return _extract_model_council_study_packet(cast(Dict[str, Any], latest_signal))
    return None


def _current_or_synthesized_model_council_study_packet(
    payload: Dict[str, Any],
    *,
    now: Optional[float] = None,
    max_packet_age_seconds: float = DEFAULT_MAX_SIGNAL_AGE_SECONDS,
) -> Optional[Dict[str, Any]]:
    def _packet_matches_latest_capture(packet: Mapping[str, Any]) -> bool:
        latest_capture_epoch = _coerce_finite_float(
            payload.get("last_capture_epoch") or payload.get("last_capture_started_epoch")
        )
        packet_created_epoch = _coerce_finite_float(packet.get("created_epoch") or packet.get("created_epoch_sec"))
        if latest_capture_epoch is None or packet_created_epoch is None:
            return True
        return float(packet_created_epoch) + 0.25 >= float(latest_capture_epoch)

    packet = _extract_model_council_study_packet(payload)
    if isinstance(packet, dict) and _v3_study_packet_is_current(
        packet,
        now=now,
        max_packet_age_seconds=max_packet_age_seconds,
    ) and _packet_matches_latest_capture(packet):
        return packet
    synthesized = _synthesize_model_council_study_packet(payload)
    if isinstance(synthesized, dict) and _v3_study_packet_is_current(
        synthesized,
        now=now,
        max_packet_age_seconds=max_packet_age_seconds,
    ):
        return synthesized
    return None


def fetch_latest_model_council_packet(
    base_url: str,
    session_id: str,
    timeout: float = DEFAULT_MODEL_COUNCIL_FETCH_TIMEOUT_SECONDS,
) -> Optional[Dict[str, Any]]:
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
    paths = (
        f"/v1/mobile/model-council/sessions/{session_q}/execution/latest",
        f"/v1/mobile/model-council/execution/latest?session_id={session_q}",
        f"/v1/mobile/runtime/trace/v3?session_id={session_q}",
    )
    for path in paths:
        try:
            payload = _get_json(path)
            if not isinstance(payload, dict):
                continue
            packet = _extract_model_council_packet(payload)
            if isinstance(packet, dict):
                return packet
        except urllib.error.HTTPError as exc:
            if getattr(exc, "code", None) not in {404, 405}:
                LOGGER.debug("V3 packet fetch failed from %s: %s", path, exc)
        except urllib.error.URLError as exc:
            LOGGER.debug("V3 packet fetch failed from %s: %s", path, exc)
        except Exception as exc:
            LOGGER.debug("V3 packet parse failed from %s: %s", path, exc)
    return None


def fetch_latest_model_council_study_packet(
    base_url: str,
    session_id: str,
    timeout: float = DEFAULT_MODEL_COUNCIL_FETCH_TIMEOUT_SECONDS,
    *,
    max_packet_age_seconds: float = DEFAULT_MAX_SIGNAL_AGE_SECONDS,
) -> Optional[Dict[str, Any]]:
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
    paths = (
        f"/v1/mobile/model-council/sessions/{session_q}/study/latest",
        f"/v1/mobile/model-council/study/latest?session_id={session_q}",
        f"/v1/mobile/model-council/sessions/{session_q}/latest",
        f"/v1/mobile/model-council/latest?session_id={session_q}",
    )
    for path in paths:
        try:
            payload = _get_json(path)
            if not isinstance(payload, dict):
                continue
            packet = _current_or_synthesized_model_council_study_packet(
                payload,
                max_packet_age_seconds=max_packet_age_seconds,
            )
            if isinstance(packet, dict):
                return packet
            LOGGER.debug("V3 study packet from %s ignored because it is stale or incomplete.", path)
        except urllib.error.HTTPError as exc:
            if getattr(exc, "code", None) not in {404, 405}:
                LOGGER.debug("V3 study packet fetch failed from %s: %s", path, exc)
        except urllib.error.URLError as exc:
            LOGGER.debug("V3 study packet fetch failed from %s: %s", path, exc)
        except Exception as exc:
            LOGGER.debug("V3 study packet parse failed from %s: %s", path, exc)
    return None


def _write_shooter_handshake(
    *,
    session_id: str,
    base_url: str,
    decision: Mapping[str, Any],
    packet: Optional[Mapping[str, Any]],
    tracker_snapshot: Optional[Mapping[str, Any]],
    selected_window_hwnd: Optional[int] = None,
    preferred_window_hwnd: Optional[int] = None,
) -> None:
    try:
        packet_payload = _v3_mapping(packet)
        tracker_payload = _v3_mapping(tracker_snapshot)
        execution = _v3_mapping(packet_payload.get("execution"))
        council = _v3_mapping(packet_payload.get("model_council"))
        promotion = _v3_mapping(packet_payload.get("promotion_trace"))
        lane_context = _v3_execution_lane_context(packet_payload, council, promotion)
        tracker_execution_packet = _extract_model_council_packet(tracker_payload, now=time.time()) if tracker_payload else None
        authority_packet_id = _v3_packet_id(tracker_execution_packet) if isinstance(tracker_execution_packet, Mapping) else ""
        validation_packet_id = str(decision.get("packet_id") or packet_payload.get("packet_id") or "").strip()
        packet_type = str(
            packet_payload.get("packet_type")
            or ("PG_EXECUTION_PACKET_V3" if str(packet_payload.get("schema_version") or "") == PG_EXECUTION_PACKET_SCHEMA_V3 else "")
        ).strip()
        action_sequence_report: Optional[dict[str, Any]] = None
        if _last_action_sequence_result is not None:
            candidate_report = _last_action_sequence_result.as_dict()
            current_packet_id = str(decision.get("packet_id") or packet_payload.get("packet_id") or "").strip()
            action_packet_id = str(candidate_report.get("packet_id") or "").strip()
            if packet_type == "PG_EXECUTION_PACKET_V3" and action_packet_id and action_packet_id == current_packet_id:
                action_sequence_report = candidate_report
        handshake = {
            "session_id": str(session_id or "").strip(),
            "base_url": str(base_url or "").strip(),
            "timestamp_epoch": time.time(),
            "selected_window_hwnd": int(selected_window_hwnd or 0) or None,
            "preferred_window_hwnd": int(preferred_window_hwnd or 0) or None,
            "window_matches_preferred": (
                bool(int(selected_window_hwnd or 0))
                and bool(int(preferred_window_hwnd or 0))
                and int(selected_window_hwnd or 0) == int(preferred_window_hwnd or 0)
            ),
            "packet_seen": bool(authority_packet_id or validation_packet_id),
            "packet_id": authority_packet_id or validation_packet_id,
            "authority_packet_id": authority_packet_id,
            "validation_packet_id": validation_packet_id,
            "packet_under_validation_id": validation_packet_id if validation_packet_id != authority_packet_id else "",
            "packet_type": packet_type or decision.get("packet_type") or "MISSING",
            "execution_packet_present": bool(authority_packet_id) or packet_type == "PG_EXECUTION_PACKET_V3",
            "study_packet_present": packet_type == "STUDY_PACKET",
            "execution_state": decision.get("execution_state") or execution.get("state") or council.get("final_state"),
            "side": decision.get("side") or execution.get("side") or council.get("final_side"),
            "reason": decision.get("reason"),
            "runtime_integrity": decision.get("runtime_integrity"),
            "gate_1_second_read": decision.get("gate_1_second_read"),
            "gate_2_trade_discipline": decision.get("gate_2_trade_discipline"),
            "gate_3_model_council": decision.get("gate_3_model_council"),
            "calibration": decision.get("calibration"),
            "will_click": bool(decision.get("will_click")),
            "candidate_id": decision.get("candidate_id") or promotion.get("candidate_id"),
            "candidate_stage": decision.get("candidate_stage") or promotion.get("candidate_stage"),
            "final_score": decision.get("final_score") or promotion.get("final_execution_score") or council.get("final_execution_score"),
            "execution_threshold": decision.get("execution_threshold") or promotion.get("execution_threshold") or council.get("execution_threshold"),
            "true_blocker": decision.get("true_blocker") or promotion.get("true_blocker") or promotion.get("blocked_by"),
            "next_required": decision.get("next_required") or promotion.get("next_required"),
            "selected_execution_lane": decision.get("selected_execution_lane") or lane_context["selected_execution_lane"],
            "execution_lane": decision.get("execution_lane") or lane_context["execution_lane"],
            "lane_accepted": decision.get("lane_accepted") if decision.get("lane_accepted") is not None else lane_context["lane_accepted"],
            "accepted_lanes": decision.get("accepted_lanes") or lane_context["accepted_lanes"],
            "current_candle_acceptance": decision.get("current_candle_acceptance") or lane_context["current_candle_acceptance"],
            "action_sequence": action_sequence_report,
            "tracker_state_version": tracker_payload.get("state_version"),
            "tracker_decision_version": tracker_payload.get("decision_version"),
            "tracker_status": tracker_payload.get("status"),
        }
        _SHOOTER_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(handshake, sort_keys=True, indent=2, default=str)
        tmp_path = _SHOOTER_HANDSHAKE_PATH.with_name(
            f"{_SHOOTER_HANDSHAKE_PATH.name}.{os.getpid()}.{threading.get_ident()}.{time.monotonic_ns()}.tmp"
        )
        tmp_path.write_text(payload, encoding="utf-8")
        for attempt in range(4):
            try:
                tmp_path.replace(_SHOOTER_HANDSHAKE_PATH)
                return
            except PermissionError:
                if attempt >= 3:
                    break
                time.sleep(0.05 * (attempt + 1))
        try:
            _SHOOTER_HANDSHAKE_PATH.write_text(payload, encoding="utf-8")
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
    except Exception as exc:
        LOGGER.debug("Failed to write shooter handshake: %s", exc)


def _v3_live_broker_clicks_explicitly_enabled() -> bool:
    return str(os.getenv(LIVE_BROKER_CLICK_ENV, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _shooter_endpoint_probes_enabled() -> bool:
    return str(os.getenv("PHOENIXGUARD_SHOOTER_ENDPOINT_PROBES", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}


def _shooter_execution_endpoint_listener_enabled() -> bool:
    raw = os.getenv("PHOENIXGUARD_SHOOTER_EXECUTION_ENDPOINT_LISTENER")
    if raw is None:
        raw = os.getenv("PHOENIXGUARD_SHOOTER_ENDPOINT_PROBES", "1")
    return str(raw or "0").strip().lower() in {"1", "true", "yes", "on"}


def _shooter_study_endpoint_probes_enabled() -> bool:
    raw = os.getenv("PHOENIXGUARD_SHOOTER_STUDY_ENDPOINT_PROBES")
    if raw is None:
        raw = os.getenv("PHOENIXGUARD_SHOOTER_ENDPOINT_PROBES", "0")
    return str(raw or "0").strip().lower() in {"1", "true", "yes", "on"}


def _live_step_screenshots_enabled(*, record_action_evidence: bool, live_behavior_validation: bool, skip_side_click: bool) -> bool:
    if live_behavior_validation or skip_side_click:
        return bool(record_action_evidence or live_behavior_validation)
    screenshot_env = str(os.getenv("PHOENIXGUARD_LIVE_STEP_SCREENSHOTS", "") or "").strip().lower()
    return bool(record_action_evidence and screenshot_env in {"1", "true", "yes", "on"})


def execute_v3_packet_trade(
    hwnd: int,
    boxes: Dict[str, Dict[str, Any]],
    packet: Mapping[str, Any],
    *,
    allow_live_clicks: bool = False,
    broker_timing_profile_path: Optional[str] = None,
    action_speed: str = "balanced",
    record_action_evidence: bool = False,
    live_behavior_validation: bool = False,
    behavior_report_mode: str = "LIVE_BEHAVIOR_VALIDATION",
    session_id: str = "",
    time_button_wait_override_ms: Optional[int] = None,
    skip_side_click: bool = False,
    action_status_callback: Optional[Any] = None,
) -> bool:
    global _last_action_sequence_result
    _last_action_sequence_result = None
    if not (bool(allow_live_clicks) and _v3_live_broker_clicks_explicitly_enabled()):
        LOGGER.error(
            "V3 live broker click refused: validation default is %s and %s is not explicitly enabled",
            DEFAULT_V3_SHOOTER_MODE,
            LIVE_BROKER_CLICK_ENV,
        )
        return False
    side = _v3_packet_side(packet)
    expiry = _v3_packet_expiry_seconds(packet)
    if side not in {"BUY", "SELL"} or expiry <= 0:
        LOGGER.error("V3 execute refused: invalid side or expiry in packet_id=%s", _v3_packet_id(packet))
        return False
    if not skip_side_click:
        validation = validate_execution_packet_v3(
            packet,
            now_epoch=time.time(),
            require_executable=True,
            require_broker_click_safe_identity=True,
        )
        if not validation.ok:
            LOGGER.error(
                "V3 execute refused: packet_id=%s failed direct live-click validation reason=%s",
                _v3_packet_id(packet),
                validation.first_reason,
            )
            return False
    profile_path = broker_timing_profile_path or str(DEFAULT_BROKER_TIMING_PROFILE_FILE)
    try:
        timing_profile = BrokerTimingProfile.from_file(profile_path, action_speed=action_speed)
    except Exception as exc:
        LOGGER.warning("V3 sequencer: failed to load timing profile %s: %s; using built-in balanced defaults", profile_path, exc)
        timing_profile = BrokerTimingProfile().with_speed(action_speed)
    if time_button_wait_override_ms is not None and int(time_button_wait_override_ms) > 0:
        timing_profile = replace(timing_profile, time_button_after_click_wait_ms=int(time_button_wait_override_ms))
    packet_id = _v3_packet_id(packet) or f"packet_{int(time.time() * 1000)}"
    rect_for_expiry_cache = get_window_rect(hwnd)
    cached_expiry = _get_cached_confirmed_expiry(hwnd, rect_for_expiry_cache, int(expiry))
    if cached_expiry is not None:
        LOGGER.info("V3 sequencer: using confirmed broker expiry cache=%ss for packet_id=%s", cached_expiry, packet_id)

    def time_reader(read_hwnd: int, read_boxes: Mapping[str, Any]) -> Optional[int]:
        if cached_expiry is not None and int(cached_expiry) == int(expiry):
            return int(cached_expiry)
        return ocr_read_time_region(read_hwnd, cast(Dict[str, Dict[str, Any]], dict(read_boxes)))

    evidence_enabled = _live_step_screenshots_enabled(
        record_action_evidence=bool(record_action_evidence),
        live_behavior_validation=bool(live_behavior_validation),
        skip_side_click=bool(skip_side_click),
    )
    if record_action_evidence and not evidence_enabled:
        LOGGER.info(
            "V3 live action trace is enabled without step screenshots; set PHOENIXGUARD_LIVE_STEP_SCREENSHOTS=1 for full screenshot capture."
        )
    evidence_recorder = ActionEvidenceRecorder(
        _SHOOTER_RUNTIME_DIR / "action_evidence",
        enabled=evidence_enabled,
        packet_id=packet_id,
    )
    sequencer = ShooterActionSequencerV2(
        hwnd=hwnd,
        boxes=boxes,
        get_window_rect=get_window_rect,
        activate_window=activate_window,
        validate_calibration=validate_calibration,
        is_broker_ready=is_broker_ready,
        adapter=LowLevelActionAdapter(pyautogui),
        ensure_foreground_window=ensure_window_foreground,
        is_foreground_window=is_window_foreground,
        timing_profile=timing_profile,
        evidence_recorder=evidence_recorder,
        ocr_reader=time_reader,
        status_callback=action_status_callback,
        logger=LOGGER,
    )
    result = sequencer.execute(packet, side=side, expiry_seconds=int(expiry), skip_side_click=bool(skip_side_click))
    _last_action_sequence_result = result
    if result.overall in {"PASS", "PASS_TIME_ONLY"} and result.expiry_status != "UNVERIFIED_ABORT":
        _remember_confirmed_expiry(hwnd, get_window_rect(hwnd), int(expiry), source=str(result.method or "sequencer"))
    LOGGER.info(
        "V3 action sequencer result: overall=%s reason=%s method=%s expiry_status=%s steps=%s",
        result.overall,
        result.reason,
        result.method,
        result.expiry_status,
        len(result.steps),
    )
    if live_behavior_validation:
        initial_rect = sequencer.initial_rect
        final_rect_obj = get_window_rect(hwnd)
        final_rect = rect_bounds(final_rect_obj) if final_rect_obj is not None else None
        try:
            write_live_behavior_validation_report(
                result,
                session_id=session_id or str(packet.get("session_id") or ""),
                mode=str(behavior_report_mode or "LIVE_BEHAVIOR_VALIDATION"),
                calibration_file=str(BOXES_FILE),
                broker_window_title=_window_title(hwnd),
                window_rect_initial=initial_rect,
                window_rect_final=final_rect,
                json_path=_SHOOTER_RUNTIME_DIR / "live_behavior_validation_report.json",
                md_path=_SHOOTER_RUNTIME_DIR / "live_behavior_validation_report.md",
            )
        except Exception as exc:
            LOGGER.warning("V3 live behavior validation report failed: %s", exc)
    if result.clicked:
        LOGGER.info("execute: CLICK SENT => %s via ShooterActionSequencerV2", side)
    if skip_side_click and result.overall == "PASS_TIME_ONLY":
        LOGGER.info("execute: time-only calibration sequence completed; side click skipped")
        return True
    return bool(result.clicked)

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
        payload.get("action"),
        payload.get("side"),
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


def _timing_profile_current_flow_ready(profile: Mapping[str, Any], side: Optional[str] = None) -> bool:
    target = _normalize_trade_side(side)
    profile_side = _normalize_trade_side(profile.get("side") or profile.get("candidate_side"))
    if target in {"BUY", "SELL"} and profile_side in {"BUY", "SELL"} and target != profile_side:
        return False
    raw_ready = profile.get("current_flow_continuation_ready")
    if raw_ready is True:
        return True
    if isinstance(raw_ready, str) and raw_ready.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return False


def _entry_location_current_flow_continuation_ready(
    payload: Dict[str, Any],
    tracking_summary: Mapping[str, Any],
    target: str,
    *,
    history_values: Sequence[float],
    close_position: float,
    micro_event: str,
) -> bool:
    side = _normalize_trade_side(target)
    if side not in {"BUY", "SELL"}:
        return False

    timing_profile_any = payload.get("execution_timing")
    if isinstance(timing_profile_any, Mapping) and _timing_profile_current_flow_ready(timing_profile_any, side):
        return True
    tracking_timing_any = tracking_summary.get("execution_timing")
    if isinstance(tracking_timing_any, Mapping) and _timing_profile_current_flow_ready(tracking_timing_any, side):
        return True

    kernel_any = payload.get("decision_kernel")
    if not isinstance(kernel_any, Mapping):
        kernel_any = tracking_summary.get("decision_kernel")
    if not isinstance(kernel_any, Mapping):
        return False
    kernel = cast(Mapping[str, Any], kernel_any)

    if str(kernel.get("trade_mode", "") or "").strip().upper() != "TREND_FOLLOW":
        return False
    kernel_state = str(kernel.get("state", payload.get("setup_state", "")) or "").strip().upper()
    kernel_decision = str(kernel.get("decision", "") or "").strip().upper()
    next_event = str(kernel.get("next_most_likely_event", "") or "").strip().lower()
    immediate_trigger = bool(
        kernel_state in {"ARMED", "TRIGGERED", "ACTIVE"}
        and (
            next_event in {"trigger", "target"}
            or kernel_state in {"TRIGGERED", "ACTIVE"}
            or "TRIGGER" in kernel_decision
            or "EXECUTE" in kernel_decision
        )
    )
    if not immediate_trigger:
        return False

    if not history_values:
        return False
    history_extension = bool(
        side == "BUY" and max(history_values) >= ENTRY_LOCATION_BUY_HISTORY_MAX_POSITION
    ) or bool(
        side == "SELL" and min(history_values) <= ENTRY_LOCATION_SELL_HISTORY_MIN_POSITION
    )
    if not history_extension:
        return False
    history_stretched = bool(
        side == "BUY" and max(history_values) >= ENTRY_LOCATION_BUY_HISTORY_STRETCH_POSITION
    ) or bool(
        side == "SELL" and min(history_values) <= ENTRY_LOCATION_SELL_HISTORY_STRETCH_POSITION
    )

    p_trigger_next_1 = _clip_unit_float(kernel.get("p_trigger_next_1")) or 0.0
    p_trigger_next_3 = _clip_unit_float(kernel.get("p_trigger_next_3")) or p_trigger_next_1
    p_target = _clip_unit_float(kernel.get("p_target_before_invalidation")) or 0.0
    p_expire = _clip_unit_float(kernel.get("p_expire_before_trigger")) or 0.0
    try:
        target_horizon_candles = int(kernel.get("target_horizon_candles", 1) or 1)
    except (TypeError, ValueError):
        target_horizon_candles = 1
    try:
        eta_target_candles = int(kernel.get("eta_target_after_trigger_candles", target_horizon_candles) or target_horizon_candles)
    except (TypeError, ValueError):
        eta_target_candles = target_horizon_candles
    hazard_trigger = _clip_unit_float(kernel.get("hazard_trigger"))
    if hazard_trigger is None:
        hazard_trigger = p_trigger_next_1
    hazard_invalidation = _clip_unit_float(kernel.get("hazard_invalidation"))
    if hazard_invalidation is None:
        hazard_invalidation = max(0.0, 1.0 - p_target)

    min_p1 = 0.84 if history_stretched else ENTRY_LOCATION_CURRENT_FLOW_MIN_P1
    min_p3 = 0.94 if history_stretched else ENTRY_LOCATION_CURRENT_FLOW_MIN_P3
    min_target = 0.68 if history_stretched else ENTRY_LOCATION_CURRENT_FLOW_MIN_TARGET
    min_alignment = ENTRY_LOCATION_CURRENT_FLOW_MIN_ALIGNMENT + (1 if history_stretched else 0)

    if p_trigger_next_1 < min_p1 or p_trigger_next_3 < min_p3 or p_target < min_target:
        return False
    if eta_target_candles < 2:
        return False
    if p_expire > ENTRY_LOCATION_CURRENT_FLOW_MAX_EXPIRE:
        return False
    if hazard_trigger <= 0.0 or hazard_trigger < max(0.08, hazard_invalidation * 1.10):
        return False

    alignment_values = (
        payload.get("execution_action"),
        payload.get("action"),
        payload.get("candidate_action"),
        payload.get("scenario_top_direction"),
        payload.get("major_bias"),
        payload.get("bias_direction"),
        tracking_summary.get("global_direction"),
        tracking_summary.get("local_direction"),
        tracking_summary.get("impulse_direction"),
        kernel.get("major_trend_side"),
        kernel.get("dominant_side"),
        kernel.get("candle_execution_side"),
        kernel.get("next_candle_bias"),
    )
    alignment_count = 0
    conflict_count = 0
    for value in alignment_values:
        parsed = _normalize_trade_side(value)
        if parsed == side:
            alignment_count += 1
        elif parsed in {"BUY", "SELL"}:
            conflict_count += 1
    if alignment_count < min_alignment or conflict_count > 0:
        return False

    micro_text = str(micro_event or "").strip().lower()
    if micro_text:
        bearish_flow = "bear" in micro_text or "sell" in micro_text or "down" in micro_text or "break" in micro_text
        bullish_flow = "bull" in micro_text or "buy" in micro_text or "up" in micro_text or "break" in micro_text
        if side == "SELL" and not bearish_flow:
            return False
        if side == "BUY" and not bullish_flow:
            return False

    if side == "SELL" and close_position > ENTRY_LOCATION_SELL_MIN_CLOSE_POSITION:
        return True
    if side == "BUY" and close_position < ENTRY_LOCATION_BUY_MAX_CLOSE_POSITION:
        return True
    return True


def _entry_location_allows_trade(payload: Dict[str, Any], side: str) -> Tuple[bool, str]:
    target = _normalize_trade_side(side)
    if target not in {"BUY", "SELL"}:
        return False, "unsupported side"

    tracking_summary = payload.get("tracking_summary")
    if not isinstance(tracking_summary, dict):
        execution_payload = payload.get("execution")
        if isinstance(execution_payload, Mapping):
            tracking_summary = execution_payload.get("tracking_summary")
    if not isinstance(tracking_summary, dict):
        council_payload = payload.get("model_council")
        if isinstance(council_payload, Mapping):
            tracking_summary = council_payload.get("tracking_summary")
    if not isinstance(tracking_summary, dict):
        return True, "no tracking summary available"

    tracking_dict = cast(Dict[str, Any], tracking_summary)
    behavior = tracking_dict.get("behavior")
    latest_token: Dict[str, Any] = {}
    if isinstance(behavior, dict):
        token_rows = behavior.get("candle_tokens")
        if isinstance(token_rows, list) and token_rows:
            last_token = token_rows[-1]
            if isinstance(last_token, dict):
                latest_token = cast(Dict[str, Any], last_token)

    close_position = _coerce_nonnegative_seconds(latest_token.get("close_position"))
    if close_position is None:
        close_position = _coerce_nonnegative_seconds(payload.get("close_position"))
    if close_position is None:
        LOGGER.debug("entry_gate: no close_position context available; accepting by default")
        return True, "no close_position context available"

    micro_event = str(
        latest_token.get("micro_structure_event")
        or payload.get("micro_structure_event")
        or tracking_dict.get("micro_structure_event")
        or ""
    ).strip().lower()

    support_resistance_zones = _support_resistance_zones_for_entry_gate(payload, tracking_dict)
    favorable_zone = False
    favorable_zone_score = 0.0
    zone_reason = "no sr zone context"
    if support_resistance_zones:
        wanted_role = "support" if target == "BUY" else "resistance"
        wanted_relations = {"below_price", "at_price"} if target == "BUY" else {"above_price", "at_price"}
        wanted_relevance = "entry_support" if target == "BUY" else "entry_resistance"
        target_zone_candidates: List[Tuple[float, Dict[str, Any]]] = []
        for raw_zone in support_resistance_zones:
            zone = dict(raw_zone)
            text = " ".join(
                str(zone.get(key, "") or "").strip().lower()
                for key in ("role", "label", "key", "entry_relevance", "kind", "zone_type", "type")
            )
            role = "support" if "support" in text else "resistance" if "resistance" in text else ""
            if not role:
                direction = _normalize_trade_side(zone.get("direction"))
                role = "support" if direction == "BUY" else "resistance" if direction == "SELL" else ""
            candidate_side = _normalize_trade_side(zone.get("candidate_side") or zone.get("direction"))
            price_relation = str(zone.get("price_relation") or "").strip().lower()
            entry_relevance = str(zone.get("entry_relevance") or "").strip().lower()
            if role and role != wanted_role:
                continue
            if price_relation and price_relation not in wanted_relations and entry_relevance != wanted_relevance:
                continue
            if candidate_side and candidate_side not in {target, ""} and entry_relevance != wanted_relevance:
                continue
            significance = max(
                _clip_unit_float(zone.get("significance_score")) or 0.0,
                _clip_unit_float(zone.get("historical_significance")) or 0.0,
                _clip_unit_float(zone.get("confidence")) or 0.0,
            )
            historical_significance = _clip_unit_float(zone.get("historical_significance")) or significance
            distance = _clip_unit_float(zone.get("distance_to_latest_norm"))
            if distance is None:
                distance = 1.0
            if (
                price_relation != "at_price"
                and distance > ENTRY_LOCATION_ENTRY_AREA_NEAR_DISTANCE
                and significance < 0.56
                and not bool(zone.get("still_significant", False))
            ):
                continue
            proximity = max(0.0, min(1.0, 1.0 - distance))
            relation_bonus = 0.18 if price_relation == "at_price" else 0.08 if price_relation in wanted_relations else 0.0
            relevance_bonus = 0.10 if entry_relevance == wanted_relevance else 0.0
            persistence_bonus = 0.10 if bool(zone.get("still_significant", False)) else 0.0
            score = max(
                0.0,
                min(
                    1.0,
                    0.38 * significance
                    + 0.22 * historical_significance
                    + 0.22 * proximity
                    + relation_bonus
                    + relevance_bonus
                    + persistence_bonus,
                ),
            )
            if score >= ENTRY_LOCATION_SIGNIFICANT_ENTRY_MIN_SCORE:
                zone["entry_area_score"] = round(score, 4)
                zone["entry_area_role"] = role or wanted_role
                target_zone_candidates.append((score, zone))
        if target_zone_candidates:
            target_zone_candidates.sort(
                key=lambda item: (
                    -float(item[0]),
                    float(item[1].get("distance_to_latest_norm", 1.0) or 1.0),
                )
            )
            favorable_zone_score, zone = target_zone_candidates[0]
            price_relation = str(zone.get("price_relation") or "").strip().lower()
            entry_relevance = str(zone.get("entry_relevance") or "").strip().lower()
            favorable_zone = True
            zone_reason = (
                f"zone={zone.get('label', zone.get('key', 'n/a'))} relation={price_relation or 'n/a'} "
                f"relevance={entry_relevance or 'n/a'} score={favorable_zone_score:.2f}"
            )

    history_position = _entry_history_position_context(payload, tracking_dict)
    try:
        history_sample_size = int(history_position.get("sample_size", 0) or 0)
    except (TypeError, ValueError):
        history_sample_size = 0
    history_global_position = _clip_unit_float(history_position.get("global_position"))
    history_local_position = _clip_unit_float(history_position.get("local_position"))
    history_values = [
        value
        for value in (history_global_position, history_local_position)
        if value is not None
    ]
    history_reason = (
        f"history global={history_global_position:.2f} local={history_local_position:.2f} sample={history_sample_size}"
        if history_global_position is not None and history_local_position is not None
        else f"history sample={history_sample_size}"
    )
    LOGGER.debug(
        "entry_gate: computed context target=%s close_position=%s favorable_zone=%s zone_reason=%s history=%s",
        target,
        close_position,
        bool(favorable_zone),
        zone_reason,
        history_reason,
    )
    pullback_at_favorable_zone = favorable_zone and micro_event in ENTRY_LOCATION_PULLBACK_EVENTS
    favorable_history_anchor = bool(
        history_sample_size >= ENTRY_LOCATION_HISTORY_MIN_SAMPLE
        and history_global_position is not None
        and history_local_position is not None
        and (
            (
                target == "BUY"
                and history_global_position <= ENTRY_LOCATION_BUY_FAVORABLE_GLOBAL_MAX_POSITION
                and history_local_position >= 0.38
            )
            or (
                target == "SELL"
                and history_global_position >= ENTRY_LOCATION_SELL_FAVORABLE_GLOBAL_MIN_POSITION
                and history_local_position <= 0.62
            )
        )
    )
    significant_entry_context = favorable_zone or favorable_history_anchor
    current_flow_ready = _entry_location_current_flow_continuation_ready(
        payload,
        tracking_dict,
        target,
        history_values=history_values,
        close_position=float(close_position),
        micro_event=micro_event,
    )
    if history_sample_size >= ENTRY_LOCATION_HISTORY_MIN_SAMPLE and history_values:
        if target == "BUY":
            history_high_position = max(history_values)
            if history_high_position >= ENTRY_LOCATION_BUY_HISTORY_STRETCH_POSITION and not current_flow_ready:
                return False, f"buy entry is already at the studied high area position={history_high_position:.2f} ({history_reason}; {zone_reason})"
            if history_high_position >= ENTRY_LOCATION_BUY_HISTORY_MAX_POSITION and not current_flow_ready:
                return False, f"buy entry too high in studied history position={history_high_position:.2f} ({history_reason}; {zone_reason})"
        else:
            history_low_position = min(history_values)
            if history_low_position <= ENTRY_LOCATION_SELL_HISTORY_STRETCH_POSITION and not current_flow_ready:
                return False, f"sell entry is already at the studied low area position={history_low_position:.2f} ({history_reason}; {zone_reason})"
            if history_low_position <= ENTRY_LOCATION_SELL_HISTORY_MIN_POSITION and not current_flow_ready:
                return False, f"sell entry too low in studied history position={history_low_position:.2f} ({history_reason}; {zone_reason})"
        if not significant_entry_context and not current_flow_ready:
            needed = "significant support/studied-low area" if target == "BUY" else "significant resistance/studied-high area"
            return False, f"{target.lower()} entry lacks {needed} ({history_reason}; {zone_reason})"

    if target == "BUY":
        if close_position >= ENTRY_LOCATION_BUY_STRETCH_CLOSE_POSITION and micro_event not in ENTRY_LOCATION_PULLBACK_EVENTS and not current_flow_ready:
            return False, f"buy entry stretched into upper range close_position={close_position:.2f} ({zone_reason})"
        if close_position >= ENTRY_LOCATION_BUY_MAX_CLOSE_POSITION and not favorable_zone and micro_event not in ENTRY_LOCATION_PULLBACK_EVENTS and not current_flow_ready:
            return False, f"buy entry too high for support context close_position={close_position:.2f} ({zone_reason})"
    else:
        if close_position <= ENTRY_LOCATION_SELL_STRETCH_CLOSE_POSITION and micro_event not in ENTRY_LOCATION_PULLBACK_EVENTS and not current_flow_ready:
            return False, f"sell entry stretched into lower range close_position={close_position:.2f} ({zone_reason})"
        if close_position <= ENTRY_LOCATION_SELL_MIN_CLOSE_POSITION and not favorable_zone and micro_event not in ENTRY_LOCATION_PULLBACK_EVENTS and not current_flow_ready:
            return False, f"sell entry too low for resistance context close_position={close_position:.2f} ({zone_reason})"

    flow_note = " current_flow_continuation=ready" if current_flow_ready else ""
    LOGGER.info("entry_gate: ACCEPT side=%s close_position=%.2f zone_score=%.3f micro_event=%s %s", target, float(close_position), float(favorable_zone_score), micro_event or "n/a", flow_note)
    return True, f"entry location accepted close_position={close_position:.2f} {zone_reason} micro_event={micro_event or 'n/a'}{flow_note}"


def _payload_age_seconds(payload: Dict[str, Any]) -> Optional[float]:
    """Best-effort extraction of payload age in seconds."""
    for age_key in ("signal_age_sec", "signal_age_seconds", "published_age_sec", "age_sec", "age_seconds"):
        try:
            age_raw = payload.get(age_key)
            if isinstance(age_raw, (int, float)):
                return max(0.0, float(age_raw))
            if isinstance(age_raw, str) and age_raw.strip():
                parsed = float(age_raw.strip())
                return max(0.0, parsed)
        except Exception:
            pass

    epoch_candidates = (
        payload.get("published_epoch"),
        payload.get("updated_epoch"),
        payload.get("completed_epoch"),
        payload.get("timestamp_epoch"),
        payload.get("created_epoch"),
        payload.get("captured_epoch"),
    )
    for candidate in epoch_candidates:
        try:
            if isinstance(candidate, (int, float)):
                return max(0.0, time.time() - float(candidate))
            if isinstance(candidate, str) and candidate.strip():
                return max(0.0, time.time() - float(candidate.strip()))
        except Exception:
            continue

    iso_candidates = (
        payload.get("published_at"),
        payload.get("updated_at"),
        payload.get("completed_at"),
        payload.get("timestamp"),
        payload.get("created_at"),
    )
    for candidate in iso_candidates:
        try:
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            text = candidate.strip()
            parsed_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            return max(0.0, time.time() - parsed_dt.timestamp())
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

    try:
        valid_until_raw = payload.get("decision_valid_until_epoch")
        if valid_until_raw not in (None, "", 0, "0"):
            valid_until = float(valid_until_raw)
            if valid_until > 0.0 and time.time() > valid_until:
                return False
    except Exception:
        return False

    for version_key in ("state_version", "decision_version"):
        if version_key in payload:
            try:
                if int(payload.get(version_key) or 0) <= 0:
                    return False
            except Exception:
                return False

    age = _payload_age_seconds(payload)
    if age is None:
        return not ENFORCE_STRICT_EXECUTION
    freshness_window = _coerce_nonnegative_seconds(payload.get("freshness_window_sec"))
    pipeline_latency = _coerce_nonnegative_seconds(payload.get("pipeline_latency_sec"))
    dynamic_max_age = max(0.0, max_age_seconds)
    if freshness_window is not None:
        dynamic_max_age = max(dynamic_max_age, float(freshness_window))
    if pipeline_latency is not None:
        dynamic_max_age = max(dynamic_max_age, float(pipeline_latency) * 3.0)
    return age <= dynamic_max_age


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
        "action": payload.get("action"),
        "side": payload.get("side"),
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

    Strict live mode accepts only explicit PhoenixGuard execution expiry fields.
    Non-strict mode keeps the older diagnostic timing hierarchy for manual tests.

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
        strict_source = "missing"
        for key, val in _strict_execution_expiry_field_candidates(payload):
            parsed = _parse_expiry_seconds_value(val)
            if parsed and parsed > 0:
                strict_candidate = int(parsed)
                strict_source = f"explicit:{key}"
                diagnostics["explicit"] = {"field": key, "value": int(parsed)}
                break
        if strict_candidate is None:
            raise ValueError("strict expiry selection requires explicit PhoenixGuard expiry_seconds")
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
        try:
            chosen = _choose_adaptive_expiry(payload, int(requested or 0), args)
            print(json.dumps({"signal_id": payload.get("signal_id"), "requested": requested, "chosen": chosen, "payload": payload}, default=str))
        except ValueError as exc:
            if "strict expiry selection requires explicit PhoenixGuard expiry_seconds" not in str(exc):
                raise
            print(json.dumps({
                "signal_id": payload.get("signal_id"),
                "requested": requested,
                "chosen": None,
                "status": "strict_rejected",
                "reason": str(exc),
                "payload": payload,
            }, default=str))
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
        LOGGER.info("Direction=%s | Expiry=%ss | Amount=preserve broker visible amount", side, expiry)
        LOGGER.info("⏱️  Executing with Phoenix explicit expiry: %s seconds", expiry)
        LOGGER.info("Timestamp=%s", datetime.now().isoformat(timespec="seconds"))

        # Emergency halt: abort execution if global safety flag set
        if globals().get("EMERGENCY_HALT", False):
            LOGGER.error("EMERGENCY HALT: automatic trade execution is disabled. Aborting trade.")
            return False

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

        # Do NOT change amount. PhoenixGuard only sets time and clicks the selected direction.
        click_trade_button(hwnd, boxes, side)
        LOGGER.info("execute: CLICK SENT => %s", side)
        return True
    except Exception as exc:
        LOGGER.error("execute: trade shot failed with exception: %s", exc)
        return False


def run_manual(args: argparse.Namespace) -> int:
    if not bool(getattr(args, "allow_live_click", False)) or not _v3_live_broker_clicks_explicitly_enabled():
        LOGGER.error(
            "Manual live click refused. Set %s=1 and pass --allow-live-click for standalone live execution.",
            LIVE_BROKER_CLICK_ENV,
        )
        return 2

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

        def _open_time_picker() -> None:
            click_at(*rel_to_abs(rect, boxes["time_button"]["x"], boxes["time_button"]["y"]), pause=0.48)
            time.sleep(0.24)

        def _verified_or_unknown(label: str) -> bool:
            visible_seconds = ocr_read_time_region(hwnd, boxes)
            if visible_seconds is None:
                LOGGER.info("resolver: %s set attempted; OCR unavailable, proceeding with calibrated control path", label)
                return True
            if abs(int(visible_seconds) - int(expiry)) <= 2:
                LOGGER.info("resolver: %s verified visible expiry %ss", label, visible_seconds)
                return True
            LOGGER.warning(
                "resolver: %s visible expiry mismatch after set attempt: visible=%ss target=%ss",
                label,
                visible_seconds,
                expiry,
            )
            return False

        # Step 1: Open time picker
        _open_time_picker()

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
            press_key("esc")
            time.sleep(0.22)
            if _verified_or_unknown(f"exact preset {exact_key}"):
                LOGGER.info("resolver: selected exact preset %ss via %s", expiry, exact_key)
                return True
            LOGGER.warning("resolver: exact preset %s did not verify; retrying with typed/stepper controls", exact_key)
            _open_time_picker()

        if int(expiry) % 60:
            LOGGER.error(
                "resolver: expiry %ss requires second precision, but manual fallback controls only support exact presets or whole minutes",
                expiry,
            )
            return False

        # Step 3: Hourly + minute breakdown with typing
        if all(key in boxes for key in ["hourly_input", "minute_input"]):
            try:
                hours = expiry // 3600
                minutes = (expiry % 3600) // 60
                
                # Input hours
                click_at(*rel_to_abs(rect, boxes["hourly_input"]["x"], boxes["hourly_input"]["y"]), pause=0.22)
                time.sleep(0.12)
                hotkey("ctrl", "a")
                time.sleep(0.08)
                type_text_slowly(str(int(hours)), interval_sec=0.04)
                time.sleep(0.15)
                
                # Input minutes
                click_at(*rel_to_abs(rect, boxes["minute_input"]["x"], boxes["minute_input"]["y"]), pause=0.22)
                time.sleep(0.12)
                hotkey("ctrl", "a")
                time.sleep(0.08)
                type_text_slowly(str(int(minutes)), interval_sec=0.04)
                time.sleep(0.15)
                
                press_key("esc")
                time.sleep(0.22)
                if _verified_or_unknown("hourly+minute typing"):
                    LOGGER.info("resolver: set via hourly+minute typing: %dh %dm (%ds total)", hours, minutes, expiry)
                    return True
                LOGGER.warning("resolver: hourly+minute typing did not verify; retrying with +/- adjustment")
                _open_time_picker()
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
                
                press_key("esc")
                time.sleep(0.22)
                if _verified_or_unknown("+/- adjustment"):
                    LOGGER.info("resolver: set via +/- adjustment: %dh %dm (%ds total)", hours, minutes, expiry)
                    return True
                LOGGER.error("resolver: +/- adjustment did not verify visible expiry; aborting trade")
                return False
            except Exception as exc:
                LOGGER.warning("resolver: +/- adjustment failed: %s", exc)

        # Step 5: Fallback preset
        if not ENFORCE_STRICT_EXECUTION:
            fallback_key = _preset_key_for(DEFAULT_EXPIRY_FALLBACK_SECONDS)
            if fallback_key is not None:
                click_at(*rel_to_abs(rect, boxes[fallback_key]["x"], boxes[fallback_key]["y"]), pause=0.30)
                press_key("esc")
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
    def _fresh_bias_from_payload(payload: Optional[Dict[str, Any]], source_label: str) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        signal = _extract_signal_payload(payload) or payload
        if not _payload_is_fresh(signal, DEFAULT_MAX_SIGNAL_AGE_SECONDS):
            LOGGER.debug("test_signal: %s bias payload was stale; refusing execution.", source_label)
            return None
        bias, bias_source = _extract_bias_side(signal)
        if bias in {"BUY", "SELL"}:
            LOGGER.info("test_signal: Phoenix bias detected: %s from %s", bias, bias_source or source_label)
            return bias
        return None

    latest = fetch_latest_signal(base_url, session_id, timeout=timeout)
    if latest:
        bias = _fresh_bias_from_payload(latest, "latest_signal")
        if bias in {"BUY", "SELL"}:
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
        bias = _fresh_bias_from_payload(payload, "observer")
        if bias in {"BUY", "SELL"}:
            return bias
    except Exception:
        pass
    
    # Try tracker session for analysis summary
    try:
        payload = _get_json(f"/v1/mobile/window-tracker/sessions/{session_q}")
        bias = _fresh_bias_from_payload(payload, "tracker")
        if bias in {"BUY", "SELL"}:
            return bias
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


def _format_expiry_target_text(expiry_seconds: int) -> str:
    seconds_total = max(1, int(expiry_seconds))
    hours = seconds_total // 3600
    minutes = (seconds_total % 3600) // 60
    seconds = seconds_total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _build_calibration_test_packet(
    *,
    session_id: str,
    side: str,
    expiry_seconds: int,
    signal_id: str = "",
    base_url: str = "",
) -> Dict[str, Any]:
    now_epoch = time.time()
    target_text = _format_expiry_target_text(expiry_seconds)
    decision_id = f"calibration_test_{int(now_epoch * 1000)}"
    return {
        "schema_version": PG_EXECUTION_PACKET_SCHEMA_V3,
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": decision_id,
        "session_id": session_id,
        "source": "isolated_calibration_test_sequencer",
        "signal_id": signal_id,
        "base_url": base_url,
        "created_epoch": now_epoch,
        "valid_until_epoch": now_epoch + 300.0,
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": side,
            "expiry_seconds": int(expiry_seconds),
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
            "time_sequence": {
                "mode": "TYPE_OR_ADJUST",
                "target_seconds": int(expiry_seconds),
                "target_text": target_text,
                "steps": [
                    {"action": "click_calibrated_time_button"},
                    {"action": "wait_before_time_filling"},
                    {"action": "focus_hourly_input", "value": target_text[0:2]},
                    {"action": "focus_minute_input", "value": target_text[3:5]},
                    {"action": "confirm_time"},
                    {"action": "click_calibrated_side_button", "side": side},
                ],
            },
        },
        "model_council": {
            "final_state": "EXECUTABLE",
            "final_side": side,
            "decision_id": decision_id,
            "reason": "isolated_calibration_test_uses_shooter_action_sequencer_v2",
            "contributors_are_diagnostic": True,
        },
        "runtime_model_health": {
            "all_required_models_awake": True,
            "council_status": "CALIBRATION_TEST",
        },
    }


def _fetch_live_startup_prime_expiry(base_url: str, session_id: str, *, fallback_seconds: int = DEFAULT_STARTUP_PRIME_EXPIRY_SECONDS) -> int:
    session_q = urllib.parse.quote(str(session_id or ""))
    base = str(base_url or DEFAULT_BASE_URL).rstrip("/")
    paths = (
        f"/v1/mobile/live/state/v3/{session_q}?mode=BROKER",
        f"/v1/mobile/window-tracker/sessions/{session_q}",
    )

    def read_json(path: str) -> Optional[Dict[str, Any]]:
        try:
            with urllib.request.urlopen(f"{base}{path}", timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    def candidates(payload: Mapping[str, Any]) -> List[Any]:
        broker_execution = payload.get("broker_execution_state")
        execution_controls = payload.get("execution_controls")
        if not isinstance(broker_execution, Mapping):
            broker_execution = {}
        if not isinstance(execution_controls, Mapping):
            execution_controls = {}
        return [
            execution_controls.get("high_frequency_expiry_seconds"),
            broker_execution.get("expiry_seconds"),
            payload.get("expiry_seconds"),
        ]

    for path in paths:
        payload = read_json(path)
        if not payload:
            continue
        for raw in candidates(payload):
            parsed = _parse_expiry_seconds_value(raw)
            if parsed is not None and int(parsed) > 0:
                return int(parsed)
    return int(fallback_seconds or DEFAULT_STARTUP_PRIME_EXPIRY_SECONDS)


def _prime_live_ready_expiry_cache(
    args: argparse.Namespace,
    *,
    hwnd: int,
    boxes: Dict[str, Dict[str, Any]],
    shooter_mode: shooter_modes.ShooterMode,
) -> bool:
    if shooter_mode not in {shooter_modes.ShooterMode.LIVE_READY, shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION}:
        return False
    if not _v3_live_broker_clicks_explicitly_enabled():
        LOGGER.info("startup_expiry_prime: skipped because %s is not enabled", LIVE_BROKER_CLICK_ENV)
        return False
    forced_expiry = _parse_expiry_seconds_value(getattr(args, "expiry", None))
    expiry = int(forced_expiry or _fetch_live_startup_prime_expiry(str(getattr(args, "base_url", DEFAULT_BASE_URL)), str(getattr(args, "session_id", ""))))
    if expiry <= 0:
        expiry = DEFAULT_STARTUP_PRIME_EXPIRY_SECONDS
    packet = _build_calibration_test_packet(
        session_id=str(getattr(args, "session_id", "")),
        side="BUY",
        expiry_seconds=int(expiry),
        signal_id="startup_expiry_prime",
        base_url=str(getattr(args, "base_url", DEFAULT_BASE_URL)),
    )
    packet["source"] = "live_ready_startup_expiry_prime"
    packet["packet_id"] = f"startup_expiry_prime_{int(time.time() * 1000)}"
    LOGGER.info("startup_expiry_prime: priming broker expiry=%ss before listening for execution packets", expiry)
    ok = execute_v3_packet_trade(
        hwnd,
        boxes,
        packet,
        allow_live_clicks=True,
        broker_timing_profile_path=str(getattr(args, "broker_speed_profile", DEFAULT_BROKER_TIMING_PROFILE_FILE)),
        action_speed=str(getattr(args, "action_speed", "balanced")),
        record_action_evidence=False,
        live_behavior_validation=False,
        session_id=str(getattr(args, "session_id", "")),
        skip_side_click=True,
    )
    if ok:
        LOGGER.info("startup_expiry_prime: confirmed broker expiry=%ss; shooter packet path is warmed", expiry)
    else:
        LOGGER.warning("startup_expiry_prime: unable to confirm expiry before listening; shooter will still verify on packet")
    return bool(ok)


def _run_startup_test_entry(
    args: argparse.Namespace,
    *,
    hwnd: int,
    boxes: Dict[str, Dict[str, Any]],
    shooter_mode: shooter_modes.ShooterMode,
    state: Dict[str, Any],
) -> bool:
    if not bool(getattr(args, "test_signal", False)):
        return False

    if shooter_mode != shooter_modes.ShooterMode.CALIBRATION_TEST:
        LOGGER.error(
            "startup_test_entry: --test-signal is isolated to --shooter-mode CALIBRATION_TEST; "
            "production V3 modes require PG_EXECUTION_PACKET_V3 only. current mode=%s",
            shooter_mode.value,
        )
        return False

    if not _v3_live_broker_clicks_explicitly_enabled():
        LOGGER.error("startup_test_entry: %s must be set to 1 before a startup broker test entry.", LIVE_BROKER_CLICK_ENV)
        return False

    base_url = str(getattr(args, "base_url", DEFAULT_BASE_URL))
    session_id = str(getattr(args, "session_id", ""))
    fallback_expiry = int(getattr(args, "expiry", TEST_SIGNAL_EXPIRY_SECONDS) or TEST_SIGNAL_EXPIRY_SECONDS)
    forced_expiry = _parse_expiry_seconds_value(getattr(args, "calibration_test_expiry", None))
    forced_side = _normalize_trade_side(getattr(args, "calibration_test_side", None))
    if forced_expiry is not None and int(forced_expiry) > 0:
        fallback_expiry = int(forced_expiry)
    timeout_sec = max(0.0, float(getattr(args, "test_signal_timeout", TEST_SIGNAL_TIMEOUT_SECONDS) or TEST_SIGNAL_TIMEOUT_SECONDS))
    poll_sec = max(0.05, float(getattr(args, "test_signal_poll", TEST_SIGNAL_POLL_INTERVAL) or TEST_SIGNAL_POLL_INTERVAL))
    time_only = bool(getattr(args, "calibration_test_time_only", False))
    if not time_only:
        LOGGER.warning(
            "startup_test_entry: CALIBRATION_TEST is time-only under V3 authority; "
            "generated calibration packets cannot click BUY/SELL."
        )
        time_only = True

    LOGGER.warning(
        "startup_test_entry: waiting up to %.1fs for a fresh Phoenix BUY/SELL startup test signal",
        timeout_sec,
    )
    deadline = time.time() + timeout_sec
    attempt = 0
    test_signal: Dict[str, Any] = {}
    while True:
        attempt += 1
        test_signal = generate_test_signal(base_url, session_id, fallback_expiry=fallback_expiry)
        if _coerce_signal_bool(test_signal.get("actionable")) is True:
            break
        remaining_sec = max(0.0, deadline - time.time())
        if remaining_sec <= 0.0:
            LOGGER.error(
                "startup_test_entry: Phoenix did not expose an actionable BUY/SELL startup test signal within %.1fs.",
                timeout_sec,
            )
            return False
        if attempt == 1 or attempt % max(1, int(5.0 / poll_sec)) == 0:
            LOGGER.info(
                "startup_test_entry: still waiting for fresh Phoenix startup test signal; status=%s",
                test_signal.get("status") or "unknown",
            )
        time.sleep(min(poll_sec, remaining_sec))

    if _coerce_signal_bool(test_signal.get("actionable")) is not True:
        LOGGER.error("startup_test_entry: Phoenix did not expose an actionable BUY/SELL startup test signal.")
        return False

    side = forced_side or _normalize_trade_side(
        test_signal.get("execution_action")
        or test_signal.get("action")
        or test_signal.get("side")
    )
    expiry = forced_expiry or _parse_expiry_seconds_value(test_signal.get("expiry_seconds"))
    if side not in {"BUY", "SELL"} or expiry is None or int(expiry) <= 0:
        LOGGER.error("startup_test_entry: invalid generated test signal side=%s expiry=%s", side, expiry)
        return False

    gate2_ok, gate2_reason, gate2_remaining = _v3_gate2_trade_discipline(state, time.time())
    if not gate2_ok and not time_only:
        LOGGER.error(
            "startup_test_entry: calibration test blocked by trade discipline: %s remaining=%ss",
            gate2_reason,
            gate2_remaining,
        )
        return False
    if not gate2_ok and time_only:
        LOGGER.warning(
            "startup_test_entry: trade discipline is locked (%s remaining=%ss); running time-only calibration sequence with side click skipped",
            gate2_reason,
            gate2_remaining,
        )

    LOGGER.warning(
        "startup_test_entry: %s side=%s expiry=%ss",
        "RUNNING TIME-ONLY CALIBRATION SEQUENCE" if time_only else "SENDING ONE LIVE BROKER TEST ENTRY",
        side,
        int(expiry),
    )
    wait_override_seconds = getattr(args, "calibration_test_time_fill_wait", None)
    wait_override_ms: Optional[int] = None
    if wait_override_seconds is not None:
        try:
            wait_override_ms = max(1, int(float(wait_override_seconds) * 1000))
        except (TypeError, ValueError):
            wait_override_ms = None
    packet = _build_calibration_test_packet(
        session_id=session_id,
        side=side,
        expiry_seconds=int(expiry),
        signal_id=str(test_signal.get("signal_id") or ""),
        base_url=base_url,
    )
    LOGGER.warning(
        "startup_test_entry: using ShooterActionSequencerV2 typed-time path target=%s wait_before_time_fill=%sms",
        packet["execution"]["time_sequence"]["target_text"],
        wait_override_ms if wait_override_ms is not None else "profile",
    )
    clicked = execute_v3_packet_trade(
        hwnd,
        boxes,
        packet,
        allow_live_clicks=True,
        broker_timing_profile_path=str(getattr(args, "broker_speed_profile", "") or DEFAULT_BROKER_TIMING_PROFILE_FILE),
        action_speed=str(getattr(args, "action_speed", "balanced") or "balanced"),
        record_action_evidence=bool(getattr(args, "record_action_evidence", False)),
        live_behavior_validation=True,
        behavior_report_mode="CALIBRATION_TEST",
        session_id=session_id,
        time_button_wait_override_ms=wait_override_ms,
        skip_side_click=time_only,
    )
    if clicked and time_only:
        LOGGER.warning(
            "startup_test_entry: TIME-ONLY CALIBRATION COMPLETE side=%s expiry=%ss; no trade recorded",
            side,
            int(expiry),
        )
        return True
    if clicked:
        _v3_record_execution(state, packet, now=time.time())
        LOGGER.warning(
            "startup_test_entry: CALIBRATION TEST ENTRY SENT side=%s expiry=%ss; "
            "typed-time sequencer completed and trade discipline state was recorded",
            side,
            int(expiry),
        )
        return True

    LOGGER.error("startup_test_entry: live broker test entry failed before click confirmation.")
    return False



def run_signal_loop(args: argparse.Namespace) -> int:
    """V3 live signal loop with only three shooter execution gates.

    Gate 1: wait for second live read.
    Gate 2: after five executed trades, wait twenty minutes.
    Gate 3: require a Model Council EXECUTABLE packet.

    Runtime integrity remains enabled, but market reasoning is not performed in
    the shooter. Validation modes record what would happen from a fresh
    PG_EXECUTION_PACKET_V3 packet while broker clicks remain disabled by default.
    """
    global automatic_trigger_enabled
    shooter_mode = shooter_modes.resolve_shooter_mode(getattr(args, "shooter_mode", None))
    if shooter_mode.value == "LIVE_DISABLED":
        try:
            disabled_poll_floor = max(
                0.5,
                float(
                    os.getenv(
                        "PHOENIXGUARD_LIVE_DISABLED_SHOOTER_MIN_POLL_SEC",
                        str(DEFAULT_LIVE_DISABLED_SIGNAL_POLL_SECONDS),
                    )
                    or str(DEFAULT_LIVE_DISABLED_SIGNAL_POLL_SECONDS)
                ),
            )
        except ValueError:
            disabled_poll_floor = DEFAULT_LIVE_DISABLED_SIGNAL_POLL_SECONDS
        if float(getattr(args, "poll", DEFAULT_SIGNAL_POLL_SECONDS) or DEFAULT_SIGNAL_POLL_SECONDS) < disabled_poll_floor:
            LOGGER.info(
                "LIVE_DISABLED poll floor applied: %.3fs -> %.3fs to protect tracker/API display latency.",
                float(getattr(args, "poll", DEFAULT_SIGNAL_POLL_SECONDS) or DEFAULT_SIGNAL_POLL_SECONDS),
                disabled_poll_floor,
            )
            args.poll = disabled_poll_floor
    preferred_window_hwnd = int(getattr(args, "window_hwnd", 0) or 0) or None

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
        preferred_hwnd=preferred_window_hwnd,
        auto_open=bool(getattr(args, "auto_open_broker", False)),
        broker_url=str(getattr(args, "broker_url", DEFAULT_BROKER_URL)),
        allow_active_fallback=False,
    )
    resolved_base_url = _resolve_reachable_base_url(args.base_url, args.session_id, timeout=1.0)
    if resolved_base_url.rstrip("/") != args.base_url.rstrip("/"):
        LOGGER.warning("Signal API base URL auto-corrected: %s -> %s", args.base_url, resolved_base_url)
        args.base_url = resolved_base_url
    else:
        LOGGER.info("Signal API base URL: %s", args.base_url)

    boxes = load_boxes()
    preview_shown = False
    if hwnd is None:
        LOGGER.warning("Broker window is not visible yet; shooter remains armed and will wait.")
    elif not activate_window(hwnd):
        LOGGER.warning("Broker window could not be activated yet; shooter remains armed and will wait.")
        hwnd = None
    else:
        rect = get_window_rect(hwnd)
        if rect is None or not validate_calibration(boxes, rect):
            LOGGER.error("Calibration invalid or missing for the current broker window; shooter will wait before any click.")
            hwnd = None
        else:
            show_box_preview(hwnd, boxes)
            preview_shown = True

    hotkey_active = setup_hotkey_listener()
    if not hotkey_active:
        LOGGER.warning("Ctrl+B hotkey listener unavailable; automatic trigger remains %s", automatic_trigger_enabled)

    state = _three_gate_load_state()
    last_tracker_snapshot: Optional[Dict[str, Any]] = None
    last_tracker_fetch_ts = 0.0
    last_execution_packet_fetch_ts = 0.0
    last_study_packet_fetch_ts = 0.0
    leased_execution_packet: Optional[Dict[str, Any]] = None
    last_waiting_log_ts = 0.0
    last_non_actionable_status = ""
    live_validation_actions_completed = 0
    disabled_snapshot_only = shooter_mode.value == "LIVE_DISABLED"
    execution_endpoint_listener_enabled = _shooter_execution_endpoint_listener_enabled()
    study_endpoint_probes_enabled = _shooter_study_endpoint_probes_enabled()

    status_box = FloatingStatusBox(args.session_id, args.base_url)
    status_box.start()

    LOGGER.info(
        "PhoenixGuard V3 shooter live. Poll=%ss | automatic trigger=%s | shooter_mode=%s",
        args.poll,
        automatic_trigger_enabled,
        shooter_mode.value,
    )
    LOGGER.info("Active gates: Gate 1 second live read | Gate 2 five trades then 20-minute wait | Gate 3 Model Council executable packet")
    LOGGER.info("Live execution authority: PG_EXECUTION_PACKET_V3 from Model Council only")
    LOGGER.info("Disabled as shooter execution authority: raw action, decision-kernel trigger, SNIPER_READY, memory confidence, skill gates")
    LOGGER.info(
        "Broker click arm state: mode=%s | env_%s=%s | default=%s",
        shooter_mode.value,
        LIVE_BROKER_CLICK_ENV,
        "ON" if _v3_live_broker_clicks_explicitly_enabled() else "OFF",
        DEFAULT_V3_SHOOTER_MODE,
    )
    LOGGER.info("Press Ctrl+B to toggle automatic trade execution")

    if bool(getattr(args, "test_signal", False)) and hwnd is not None:
        _run_startup_test_entry(
            args,
            hwnd=hwnd,
            boxes=cast(Dict[str, Dict[str, Any]], dict(boxes)),
            shooter_mode=shooter_mode,
            state=state,
        )
    elif bool(getattr(args, "test_signal", False)):
        LOGGER.warning("startup_test_entry: skipped until a valid broker window is visible.")
    elif hwnd is not None:
        _prime_live_ready_expiry_cache(
            args,
            hwnd=hwnd,
            boxes=cast(Dict[str, Dict[str, Any]], dict(boxes)),
            shooter_mode=shooter_mode,
        )

    try:
        while True:
            try:
                now = time.time()
                tracker_fetch_latency = None
                if last_tracker_snapshot is None or (now - last_tracker_fetch_ts) >= DEFAULT_TRACKER_SESSION_FETCH_INTERVAL_SECONDS:
                    tracker_fetch_started = time.time()
                    refreshed_tracker_snapshot = fetch_tracker_session_snapshot(args.base_url, args.session_id)
                    if refreshed_tracker_snapshot is not None:
                        last_tracker_snapshot = refreshed_tracker_snapshot
                    tracker_fetch_latency = max(0.0, time.time() - tracker_fetch_started)
                    last_tracker_fetch_ts = now

                tracker_snapshot = last_tracker_snapshot
                fetch_started = time.time()
                max_signal_age_seconds = float(getattr(args, "max_signal_age", DEFAULT_MAX_SIGNAL_AGE_SECONDS))
                payload = _extract_model_council_packet(tracker_snapshot, now=now) if isinstance(tracker_snapshot, dict) else None
                study_payload: Optional[Dict[str, Any]] = None
                if isinstance(payload, dict) and payload:
                    leased_execution_packet = dict(payload)
                if not isinstance(payload, dict) or not payload:
                    if isinstance(tracker_snapshot, dict):
                        study_payload = _current_or_synthesized_model_council_study_packet(
                            tracker_snapshot,
                            now=now,
                            max_packet_age_seconds=max_signal_age_seconds,
                        )
                has_tracker_execution_packet = isinstance(payload, dict) and bool(payload)
                if not has_tracker_execution_packet:
                    # The execution endpoint is a first-class packet listener. The lease keeps
                    # a just-validated packet alive only through its own explicit TTL.
                    if (
                        execution_endpoint_listener_enabled
                        and not disabled_snapshot_only
                        and (now - last_execution_packet_fetch_ts) >= DEFAULT_EXECUTION_PACKET_FETCH_INTERVAL_SECONDS
                    ):
                        last_execution_packet_fetch_ts = now
                        endpoint_payload = fetch_latest_model_council_packet(
                            args.base_url,
                            args.session_id,
                            timeout=DEFAULT_ENDPOINT_PACKET_FETCH_TIMEOUT_SECONDS,
                        )
                        if isinstance(endpoint_payload, dict) and endpoint_payload:
                            endpoint_ok, endpoint_reason = _v3_runtime_integrity_check(
                                endpoint_payload,
                                expected_session_id=args.session_id,
                                now=time.time(),
                                max_packet_age_seconds=max_signal_age_seconds,
                            )
                            if endpoint_ok:
                                payload = endpoint_payload
                                study_payload = None
                                leased_execution_packet = dict(endpoint_payload)
                            else:
                                LOGGER.debug(
                                    "Discarding endpoint V3 execution packet: packet_id=%s reason=%s",
                                    _v3_packet_id(endpoint_payload),
                                    endpoint_reason,
                                )
                    if not isinstance(payload, dict) and isinstance(leased_execution_packet, dict):
                        lease_ok, lease_reason = _v3_runtime_integrity_check(
                            leased_execution_packet,
                            expected_session_id=args.session_id,
                            now=time.time(),
                            max_packet_age_seconds=max_signal_age_seconds,
                        )
                        if lease_ok:
                            payload = dict(leased_execution_packet)
                            payload["_leased_execution_packet"] = True
                            study_payload = None
                        else:
                            LOGGER.debug(
                                "Dropping leased V3 execution packet: packet_id=%s reason=%s",
                                _v3_packet_id(leased_execution_packet),
                                lease_reason,
                            )
                            leased_execution_packet = None
                signal_fetch_latency = max(0.0, time.time() - fetch_started)
                if payload is not None:
                    payload["_fetch_latency_sec"] = signal_fetch_latency
                if tracker_snapshot is not None and tracker_fetch_latency is not None:
                    tracker_snapshot["_fetch_latency_sec"] = tracker_fetch_latency

                gate2_ok, gate2_reason, lock_remaining = _v3_gate2_trade_discipline(state, time.time())
                if not isinstance(payload, dict) or not payload:
                    if (
                        not isinstance(study_payload, dict)
                        and study_endpoint_probes_enabled
                        and not disabled_snapshot_only
                        and (now - last_study_packet_fetch_ts) >= DEFAULT_STUDY_PACKET_FETCH_INTERVAL_SECONDS
                    ):
                        last_study_packet_fetch_ts = now
                        study_payload = fetch_latest_model_council_study_packet(
                            args.base_url,
                            args.session_id,
                            timeout=DEFAULT_STUDY_ENDPOINT_PACKET_FETCH_TIMEOUT_SECONDS,
                            max_packet_age_seconds=max_signal_age_seconds,
                        )
                    if isinstance(study_payload, dict):
                        study_payload["_fetch_latency_sec"] = signal_fetch_latency
                status_box.update(
                    payload if isinstance(payload, dict) else study_payload if isinstance(study_payload, dict) else None,
                    tracker_snapshot,
                    lock_remaining,
                )

                if not isinstance(payload, dict) or not payload:
                    decision = (
                        _v3_study_wait_decision(study_payload, now)
                        if isinstance(study_payload, dict) and study_payload
                        else _v3_base_decision(None, now)
                    )
                    wait_summary = str(decision.get("model_council_wait") or _model_council_wait_summary(tracker_snapshot))
                    decision["reason"] = "WAITING_FOR_EXECUTABLE_MODEL_COUNCIL_PACKET"
                    if not isinstance(study_payload, dict) or not study_payload:
                        decision["runtime_integrity"] = "WAITING"
                    decision["model_council_wait"] = wait_summary
                    _v3_log_final_decision(decision)
                    _write_shooter_handshake(
                        session_id=args.session_id,
                        base_url=args.base_url,
                        decision=decision,
                        packet=study_payload if isinstance(study_payload, dict) else None,
                        tracker_snapshot=tracker_snapshot if isinstance(tracker_snapshot, dict) else None,
                        selected_window_hwnd=hwnd,
                        preferred_window_hwnd=preferred_window_hwnd,
                    )
                    if now - last_waiting_log_ts >= 2.0:
                        LOGGER.info("waiting for PhoenixGuard V3 executable packet: %s", wait_summary)
                        last_waiting_log_ts = now
                    time.sleep(float(args.poll))
                    continue

                decision = _evaluate_v3_shooter_decision(
                    payload,
                    state,
                    boxes,
                    tracker_snapshot=tracker_snapshot if isinstance(tracker_snapshot, dict) else None,
                    expected_session_id=args.session_id,
                    now=now,
                    max_packet_age_seconds=float(getattr(args, "max_signal_age", DEFAULT_MAX_SIGNAL_AGE_SECONDS)),
                )
                if bool(decision.get("will_click")):
                    with automatic_trigger_lock:
                        trigger_enabled = automatic_trigger_enabled
                    if not trigger_enabled:
                        decision["will_click"] = False
                        decision["reason"] = "AUTOMATIC_TRIGGER_DISABLED"
                    elif hwnd is None or get_window_rect(hwnd) is None:
                        resolved_hwnd = prepare_pocket_option_window(
                            args.window_query,
                            preferred_hwnd=preferred_window_hwnd,
                            auto_open=bool(getattr(args, "auto_open_broker", False)),
                            broker_url=str(getattr(args, "broker_url", DEFAULT_BROKER_URL)),
                            allow_active_fallback=False,
                        )
                        if resolved_hwnd is None:
                            decision["will_click"] = False
                            decision["reason"] = "WAITING_FOR_BROKER_WINDOW"
                            status_box.update_action(
                                {
                                    "phase": "WAITING_FOR_BROKER_WINDOW",
                                    "step": "broker window not visible",
                                    "packet_id": decision.get("packet_id"),
                                }
                            )
                        else:
                            hwnd = int(resolved_hwnd)
                            if not activate_window(hwnd):
                                decision["will_click"] = False
                                decision["reason"] = "BROKER_WINDOW_ACTIVATION_FAILED"
                                hwnd = None
                            else:
                                rect = get_window_rect(hwnd)
                                if rect is None or not validate_calibration(boxes, rect):
                                    decision["will_click"] = False
                                    decision["reason"] = "CALIBRATION_INVALID_FOR_CURRENT_BROKER_WINDOW"
                                    hwnd = None
                                elif not preview_shown:
                                    show_box_preview(hwnd, boxes)
                                    preview_shown = True

                    if bool(decision.get("will_click")):
                        pre_click_packet = (
                            _extract_model_council_packet(tracker_snapshot, now=time.time())
                            if isinstance(tracker_snapshot, dict)
                            else None
                        )
                        if not isinstance(pre_click_packet, dict) and bool(payload.get("_leased_execution_packet")):
                            pre_click_packet = payload
                        if not isinstance(pre_click_packet, dict) and execution_endpoint_listener_enabled:
                            pre_click_packet = fetch_latest_model_council_packet(
                                args.base_url,
                                args.session_id,
                                timeout=DEFAULT_PRE_CLICK_CONFIRMATION_TIMEOUT_SECONDS,
                            )
                        pre_click_ok, pre_click_reason = _v3_pre_click_confirmation(
                            payload,
                            pre_click_packet,
                            expected_session_id=args.session_id,
                            now=time.time(),
                            max_packet_age_seconds=float(getattr(args, "max_signal_age", DEFAULT_MAX_SIGNAL_AGE_SECONDS)),
                        )
                        if not pre_click_ok:
                            decision["will_click"] = False
                            decision["reason"] = pre_click_reason
                            LOGGER.info("Pre-click confirmation blocked packet_id=%s reason=%s", decision.get("packet_id"), pre_click_reason)

                _v3_log_final_decision(decision)
                _write_shooter_handshake(
                    session_id=args.session_id,
                    base_url=args.base_url,
                    decision=decision,
                    packet=payload,
                    tracker_snapshot=tracker_snapshot if isinstance(tracker_snapshot, dict) else None,
                    selected_window_hwnd=hwnd,
                    preferred_window_hwnd=preferred_window_hwnd,
                )
                _three_gate_save_state(state)
                status_box.update(payload, tracker_snapshot, int(decision.get("discipline_remaining_seconds", 0) or 0))

                if not bool(decision.get("will_click")):
                    status_key = "|".join(
                        str(decision.get(key, "") or "")
                        for key in ("packet_id", "reason", "runtime_integrity", "gate_1_second_read", "gate_2_trade_discipline", "gate_3_model_council")
                    )
                    if status_key != last_non_actionable_status or now - last_waiting_log_ts >= 2.0:
                        last_non_actionable_status = status_key
                        LOGGER.info(
                            "V3 packet not executable by shooter: packet_id=%s reason=%s runtime=%s gate1=%s gate2=%s gate3=%s calibration=%s",
                            decision.get("packet_id"),
                            decision.get("reason"),
                            decision.get("runtime_integrity"),
                            decision.get("gate_1_second_read"),
                            decision.get("gate_2_trade_discipline"),
                            decision.get("gate_3_model_council"),
                            decision.get("calibration"),
                        )
                        last_waiting_log_ts = now
                    time.sleep(float(args.poll))
                    continue

                LOGGER.info("V3 READY: %s", decision.get("reason"))
                LOGGER.info(
                    "PROCESSING V3 PACKET IN %s: side=%s expiry=%ss packet_id=%s",
                    shooter_mode.value,
                    decision.get("side"),
                    decision.get("expiry_seconds"),
                    decision.get("packet_id"),
                )
                status_box.update_action(
                    {
                        "phase": "PACKET_ACCEPTED",
                        "step": "starting sequencer",
                        "packet_id": decision.get("packet_id"),
                        "side": decision.get("side"),
                        "expiry_seconds": decision.get("expiry_seconds"),
                    }
                )

                mode_result = _v3_apply_shooter_mode(
                    hwnd,
                    boxes,
                    payload,
                    decision,
                    state,
                    shooter_mode,
                    now=time.time(),
                    action_options={
                        "broker_timing_profile_path": str(getattr(args, "broker_speed_profile", "") or DEFAULT_BROKER_TIMING_PROFILE_FILE),
                        "action_speed": str(getattr(args, "action_speed", "balanced") or "balanced"),
                        "record_action_evidence": bool(getattr(args, "record_action_evidence", False)),
                        "session_id": str(args.session_id),
                        "max_packet_age_seconds": float(getattr(args, "max_signal_age", DEFAULT_MAX_SIGNAL_AGE_SECONDS)),
                        "time_button_wait_override_ms": (
                            max(1, int(float(getattr(args, "calibration_test_time_fill_wait", 0) or 0) * 1000))
                            if float(getattr(args, "calibration_test_time_fill_wait", 0) or 0) > 0
                            else None
                        ),
                        "action_status_callback": status_box.update_action,
                    },
                )
                if _last_action_sequence_result is not None:
                    status_box.update_action(_last_action_sequence_result.as_dict())
                if mode_result.recorded:
                    leased_execution_packet = None
                    state.pop("v3_second_live_read_baseline", None)
                    log_decision(payload, "accept", mode_result.reason, 1.0)
                    LOGGER.info("%s complete: packet_id=%s record=%s", shooter_mode.value, decision.get("packet_id"), mode_result.record_path)
                    if shooter_mode == shooter_modes.ShooterMode.LIVE_BEHAVIOR_VALIDATION:
                        live_validation_actions_completed += 1
                        max_actions = max(1, int(getattr(args, "max_live_validation_actions", 1) or 1))
                        if live_validation_actions_completed >= max_actions:
                            LOGGER.info(
                                "LIVE_BEHAVIOR_VALIDATION complete after %s action(s); stopping shooter loop",
                                live_validation_actions_completed,
                            )
                            break
                else:
                    log_decision(payload, "reject_validation_mode_failed", mode_result.reason, 0.0)
                    LOGGER.error("%s failed after V3 shooter gates passed: %s", shooter_mode.value, mode_result.reason)

                time.sleep(float(args.poll))
            except KeyboardInterrupt:
                LOGGER.info("Signal loop interrupted by user")
                break
            except Exception as exc:
                LOGGER.error("Unexpected error in V3 signal loop: %s", exc)
                time.sleep(float(args.poll))
    finally:
        status_box.stop()

    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python shooter.py",
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
    manual.add_argument(
        "--allow-live-click",
        action="store_true",
        help="Required manual safety acknowledgement for standalone live broker clicks.",
    )
    manual.set_defaults(mode="manual")

    signal = sub.add_parser("signal", help="Follow Model Council V3 packets and auto-click executable trades.")
    signal.add_argument("--session-id", default="", help="Model Council session id")
    signal.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Mobile API base URL")
    signal.add_argument("--poll", type=float, default=DEFAULT_SIGNAL_POLL_SECONDS, help="Signal poll interval seconds")
    signal.add_argument("--cooldown", type=float, default=DEFAULT_TRADE_COOLDOWN_SECONDS, help="Min seconds between clicks")
    signal.add_argument(
        "--expiry",
        type=int,
        default=0,
        help="Manual expiry override for legacy diagnostics and CALIBRATION_TEST only; production V3 packets must carry expiry.",
    )
    signal.add_argument(
        "--window-hwnd",
        type=int,
        default=0,
        help="Preferred broker window handle from BrokerSourceLockV3; title query remains the fallback.",
    )
    signal.add_argument(
        "--max-signal-age",
        type=float,
        default=DEFAULT_MAX_SIGNAL_AGE_SECONDS,
        help="Maximum age after Phoenix publishes a signal before execution is rejected",
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
    signal.add_argument("--window-query", default=None, help="Title substring to select broker window")
    signal.add_argument("--broker-url", default=DEFAULT_BROKER_URL, help="Broker URL used only when --auto-open is enabled")
    signal.add_argument(
        "--shooter-mode",
        choices=shooter_modes.SHOOTER_MODE_CHOICES,
        default=DEFAULT_V3_SHOOTER_MODE,
        help="Validation action for executable V3 packets; broker clicks remain disabled by default",
    )
    signal.add_argument(
        "--broker-speed-profile",
        default=str(DEFAULT_BROKER_TIMING_PROFILE_FILE),
        help="JSON timing profile for calibrated broker actions",
    )
    signal.add_argument(
        "--action-speed",
        choices=("conservative", "balanced", "fast-ui"),
        default="balanced",
        help="Scale calibrated action waits without changing calibration coordinates",
    )
    signal.add_argument(
        "--max-live-validation-actions",
        type=int,
        default=1,
        help="LIVE_BEHAVIOR_VALIDATION only: stop after this many V3 action sequences",
    )
    signal.add_argument(
        "--record-action-evidence",
        action="store_true",
        help="Capture before/after screenshots and action_trace.jsonl for each calibrated action step",
    )
    signal.add_argument(
        "--auto-open",
        dest="auto_open_broker",
        action="store_true",
        help="Open the broker URL automatically if no matching broker window is visible",
    )
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
        help=(
            "CALIBRATION_TEST only: send one isolated startup test entry from Phoenix bias; "
            "disabled in production V3 modes and never creates PG_EXECUTION_PACKET_V3 state"
        ),
    )
    signal.add_argument(
        "--no-test-signal",
        dest="test_signal",
        action="store_false",
        help="Skip the isolated startup calibration test entry",
    )
    signal.add_argument(
        "--calibration-test-expiry",
        dest="calibration_test_expiry",
        type=int,
        default=None,
        help="CALIBRATION_TEST only: force the isolated startup sequencer test expiry in seconds",
    )
    signal.add_argument(
        "--calibration-test-side",
        dest="calibration_test_side",
        choices=("BUY", "SELL", "buy", "sell"),
        default=None,
        help="CALIBRATION_TEST only: force the isolated startup sequencer test side instead of Phoenix bias",
    )
    signal.add_argument(
        "--calibration-test-time-fill-wait",
        dest="calibration_test_time_fill_wait",
        type=float,
        default=None,
        help="CALIBRATION_TEST only: seconds to wait after clicking time_button before filling hour/minute",
    )
    signal.add_argument(
        "--calibration-test-time-only",
        dest="calibration_test_time_only",
        action="store_true",
        help="CALIBRATION_TEST only: run the calibrated time sequence and final hold but skip BUY/SELL side click",
    )
    signal.add_argument(
        "--test-signal-timeout",
        dest="test_signal_timeout",
        type=float,
        default=TEST_SIGNAL_TIMEOUT_SECONDS,
        help="Seconds to wait for a fresh Phoenix BUY/SELL bias before the startup test entry gives up",
    )
    signal.add_argument(
        "--test-signal-poll",
        dest="test_signal_poll",
        type=float,
        default=TEST_SIGNAL_POLL_INTERVAL,
        help="Polling interval while waiting for a fresh startup test signal",
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
    signal.add_argument(
        "--min-confidence",
        dest="min_confidence",
        type=float,
        default=MIN_CONFIDENCE_TO_EXECUTE,
        help="Minimum memory-confidence (0..1) required to accept a trade (default matches agent)",
    )
    signal.add_argument(
        "--opposing-force-multiplier",
        type=float,
        default=THREE_GATE_DEFAULT_FORCE_MULTIPLIER,
        help="Adaptive Gate 3 multiplier applied to median candle range",
    )
    signal.add_argument(
        "--opposing-force-visible-fraction",
        type=float,
        default=THREE_GATE_DEFAULT_VISIBLE_RANGE_FRACTION,
        help="Adaptive Gate 3 minimum fraction of visible chart range",
    )
    signal.add_argument(
        "--opposing-force-norm-fraction",
        type=float,
        default=THREE_GATE_DEFAULT_NORMALIZED_DISTANCE_FRACTION,
        help="Adaptive Gate 3 fallback threshold when only normalized close position is available",
    )
    signal.add_argument(
        "--preferred-source",
        dest="preferred_source",
        default=AUTHORITATIVE_SIGNAL_ENDPOINT,
        help="Prefer signals from this stable source (default: model_council)",
    )
    signal.add_argument(
        "--require-preferred-source",
        dest="require_preferred_source",
        action="store_true",
        help="Require the preferred source or explicit live-execution flag before executing (avoids volatile gate mode)",
    )
    signal.set_defaults(auto_open_broker=False)
    signal.set_defaults(require_fresh_signal=True)
    # Disable kernel-trigger fallback by default to avoid implicit execution
    # decisions based on planner/kernel hints unless explicitly enabled.
    signal.set_defaults(kernel_trigger_fallback=False)
    signal.set_defaults(strict_new_signal_mode=False)  # Disabled by default: Phoenix doesn't change signal_id on state transitions
    signal.set_defaults(auto_arm_collaboration=False)
    signal.set_defaults(test_signal=False)
    signal.set_defaults(require_preferred_source=True)
    signal.set_defaults(shooter_mode=DEFAULT_V3_SHOOTER_MODE)
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
            print(f"  HWND={hwnd:<10} class={class_name:<24} title={_log_safe_text(title)}")
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
        if bool(getattr(args, "adaptive_test", False)):
            return run_adaptive_test(args)
        if not str(getattr(args, "session_id", "") or "").strip():
            parser.error("signal requires --session-id unless --adaptive-test is used")
        return run_signal_loop(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped by user")
        sys.exit(0)
