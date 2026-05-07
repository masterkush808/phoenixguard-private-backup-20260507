from __future__ import annotations
# pyright: reportUnusedImport=false, reportUnusedFunction=false, reportUnusedVariable=false, reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportGeneralTypeIssues=false, reportArgumentType=false, reportUnnecessaryIsInstance=false

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import secrets
import subprocess
import sys
import threading
import time
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, cast
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont, ImageGrab

from phoenixguard.core.config import RUNTIME
from phoenixguard.core.utils import utc_now_iso
from phoenixguard.decision.decision_kernel import analyze_decision_kernel
from phoenixguard.decision.scenario_integration import (
    predict_scenarios_from_chart_and_forecast,
    rank_scenarios_by_ensemble_agreement,
    scenarios_to_paint_layer,
)
from phoenixguard.memory.memory_features import (
    build_late_interaction_tokens,
    build_metric_profile,
    build_trajectory_signature,
    derive_entry_progression_profile,
    infer_style_signature_from_chart_state,
    late_interaction_score,
    metric_profile_alignment,
    style_alignment_score,
    trajectory_alignment,
)

from .observer import SignalObserverService


LOGGER = logging.getLogger("phoenixguard.mobile_api.window_tracker")
ArrayND = NDArray[Any]
OverlayFont = ImageFont.FreeTypeFont | ImageFont.ImageFont
ColorRGB = tuple[int, int, int]
ColorRGBA = tuple[int, int, int, int]


def _set_process_dpi_awareness_early() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if set_context is not None:
            try:
                set_context.argtypes = [wintypes.HANDLE]
                set_context.restype = wintypes.BOOL
                if bool(set_context(ctypes.c_void_p(-4))):
                    return
            except Exception:
                pass
        try:
            shcore = ctypes.windll.shcore
            set_awareness = getattr(shcore, "SetProcessDpiAwareness", None)
            if set_awareness is not None:
                set_awareness.argtypes = [ctypes.c_int]
                set_awareness.restype = ctypes.c_long
                if int(set_awareness(2)) in {0, -2147024891}:
                    return
        except Exception:
            pass
        set_process_aware = getattr(user32, "SetProcessDPIAware", None)
        if set_process_aware is not None:
            set_process_aware()
    except Exception:
        LOGGER.debug("Unable to set early DPI awareness for window tracker.", exc_info=True)


_set_process_dpi_awareness_early()

_TIMEFRAME_LABELS: tuple[str, ...] = ("M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1")
_FX_CURRENCY_CODES: frozenset[str] = frozenset({"AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"})
_POCKET_OPTION_WINDOW_FALLBACK_TOKENS = (
    "pocket option",
    "pocketoption",
    "the most innovative trading platform",
    "trading platform",
    "otc",
)
_BROWSER_TITLE_TOKENS = (
    "microsoft edge",
    "google chrome",
    "chrome",
    "mozilla firefox",
    "firefox",
    "opera",
    "browser",
)
_WINDOW_REACQUIRE_BLOCK_TOKENS = (
    "127.0.0.1",
    "localhost",
    "phoenixguard",
    "window-tracker",
    "visual studio code",
    "vscode",
    "file explorer",
    "task manager",
    "program manager",
)
_MEMORY_PRECISION_MIN_SIMILARITY = 0.72
_MEMORY_PRECISION_MIN_SCORE = 0.70
_MEMORY_PRECISION_MIN_EDGE = 0.06
_MEMORY_PRECISION_STRONG_SCORE = 0.78
_MEMORY_PRECISION_TIGHTEN_MIN = 0.58


def _normalize_fx_market_candidate(text: Any) -> str:
    raw = re.sub(r"[^A-Z0-9/ ]+", "", str(text or "").upper())
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""

    compact = raw.replace(" ", "")
    has_otc = "OTC" in compact
    compact = compact.replace("OTC", "")
    pair = ""
    slash_match = re.search(r"([A-Z]{3})/([A-Z]{3})", compact)
    if slash_match:
        left, right = slash_match.group(1), slash_match.group(2)
        if left in _FX_CURRENCY_CODES and right in _FX_CURRENCY_CODES and left != right:
            pair = f"{left}/{right}"
    if not pair:
        letters = re.sub(r"[^A-Z]", "", compact)
        for index in range(0, max(0, len(letters) - 5)):
            left = letters[index:index + 3]
            right = letters[index + 3:index + 6]
            if left in _FX_CURRENCY_CODES and right in _FX_CURRENCY_CODES and left != right:
                pair = f"{left}/{right}"
                break
    if not pair:
        return ""
    return f"{pair} OTC" if has_otc else pair
_FIXED_BROKER_AMOUNT = "5"
_EXECUTION_DEFAULT_COOLDOWN_SEC = 45.0
_EXECUTION_DEFAULT_EXPIRY_SEC = 300
_EXECUTION_MIN_LIVE_EXPIRY_SEC = 180
_EXECUTION_FAILED_ATTEMPT_BACKOFF_SEC = 60.0
_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC = 3.0
_EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC = 0.5
_EXECUTION_DEFAULT_MAX_CAPTURE_INTERVAL_SEC = 10.0
_TRACKER_ARTIFACT_RETENTION_FRAMES = 360


def _now_iso() -> str:
    return utc_now_iso()


def _now_epoch() -> float:
    return float(time.time())


def _epoch_to_utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _clip01(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return float(max(0.0, min(1.0, number)))


def _timeframe_seconds(timeframe: Any, default: int = 300) -> int:
    label = str(timeframe or "").strip().upper()
    seconds = {
        "S3": 3,
        "S15": 15,
        "S30": 30,
        "M1": 60,
        "M3": 180,
        "M5": 300,
        "M15": 900,
        "M30": 1800,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400,
    }.get(label, int(default))
    return max(1, int(seconds))


def _candles_to_seconds(candle_count: Any, timeframe: Any, default_timeframe_seconds: int = 300) -> int:
    candles = max(0, int(round(_float_or(candle_count, 0.0))))
    return int(candles * _timeframe_seconds(timeframe, default=default_timeframe_seconds))


def _float_or(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _compact_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _mapping_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items: list[Any] = list(value)
    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            result.append(_mapping_to_dict(item))
    return result


def _first_mapping(value: Any) -> dict[str, Any]:
    items = _sequence_of_mappings(value)
    return items[0] if items else {}


def _upper_action(value: Any, fallback: str = "HOLD") -> str:
    text = str(value or fallback).strip().upper()
    return text or str(fallback or "HOLD").upper()


def _opposite_action(value: Any) -> str:
    action = _upper_action(value, fallback="HOLD")
    if action == "BUY":
        return "SELL"
    if action == "SELL":
        return "BUY"
    return "HOLD"


def _friendly_phrase(value: Any, fallback: str = "--") -> str:
    text = str(value or "").strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text or fallback


def _slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._").lower()
    return slug or fallback


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload_text = json.dumps(dict(payload), ensure_ascii=True, indent=2, default=str)
    for attempt in range(6):
        tmp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            tmp_path.write_text(payload_text, encoding="utf-8")
            tmp_path.replace(path)
            return
        except (FileNotFoundError, PermissionError):
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            if attempt >= 5:
                raise
            time.sleep(0.05 * float(attempt + 1))


def _memory_bank_cache_fingerprint(bank_dir: Path) -> tuple[int, ...]:
    candidates = (
        bank_dir,
        bank_dir / "metadata.json",
        bank_dir / "stats.json",
        bank_dir / "index" / "id_map.json",
        bank_dir / "index" / "numpy_vecs.npy",
        bank_dir / "index" / "hnsw.bin",
    )
    parts: list[int] = []
    for candidate in candidates:
        try:
            stat = candidate.stat()
        except OSError:
            parts.extend((0, 0))
            continue
        parts.extend((int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(parts)


@lru_cache(maxsize=4)
def _load_phoenixguard_memory_bank_cached(bank_dir: str, fingerprint: tuple[int, ...]) -> Any | None:
    _ = fingerprint
    try:
        from phoenixguard.memory.memory_ingest import MemoryBank
    except Exception as exc:
        LOGGER.warning("PhoenixGuard memory import failed: %s", exc)
        return None
    try:
        bank = MemoryBank.load(Path(bank_dir), logger=LOGGER)
    except Exception as exc:
        LOGGER.warning("PhoenixGuard memory load failed from %s: %s", bank_dir, exc)
        return None
    if getattr(bank, "is_loaded", False):
        return bank
    return None


def _load_phoenixguard_memory_bank(bank_dir: Path) -> Any | None:
    resolved = bank_dir.resolve()
    return _load_phoenixguard_memory_bank_cached(str(resolved), _memory_bank_cache_fingerprint(resolved))


def _resolve_memory_source_path(image_path: Any) -> Path:
    raw = str(image_path or "").strip()
    if not raw:
        return Path("")
    try:
        from phoenixguard.memory import memory_ingest
    except Exception:
        return Path(raw)
    try:
        resolve_memory_image_path = getattr(memory_ingest, "_resolve_memory_image_path", None)
        if callable(resolve_memory_image_path):
            resolved_path = resolve_memory_image_path(raw)
            return Path(str(resolved_path))
    except Exception:
        pass
    return Path(raw)


def _title_matches_query(title: Any, query: Any) -> bool:
    lowered_title = str(title or "").strip().lower()
    lowered_query = str(query or "").strip().lower()
    if not lowered_query:
        return True
    if lowered_query in lowered_title:
        return True
    compact_query = _compact_text(lowered_query)
    return bool(compact_query) and compact_query in _compact_text(lowered_title)


def _window_query_fallback_tokens(query: Any) -> tuple[str, ...]:
    lowered_query = str(query or "").strip().lower()
    compact_query = _compact_text(lowered_query)
    tokens: list[str] = []
    if compact_query in {"pocketoption", "pocketoptions"} or "pocket option" in lowered_query:
        tokens.extend(_POCKET_OPTION_WINDOW_FALLBACK_TOKENS)
    elif compact_query in {"edge", "chrome", "browser", "firefox", "opera"}:
        tokens.extend(_BROWSER_TITLE_TOKENS)
    return tuple(dict.fromkeys(token for token in tokens if str(token).strip()))


def _is_pocket_option_query(query: Any) -> bool:
    lowered_query = str(query or "").strip().lower()
    compact_query = _compact_text(lowered_query)
    return (
        "pocket option" in lowered_query
        or compact_query in {"pocketoption", "pocketoptions"}
        or "the most innovative trading platform" in lowered_query
    )


def _is_pocket_option_like_title(title: Any) -> bool:
    lowered_title = str(title or "").strip().lower()
    compact_title = _compact_text(lowered_title)
    return (
        "pocket option" in lowered_title
        or "pocketoption" in compact_title
        or "the most innovative trading platform" in lowered_title
    )


def _browser_family(title: Any) -> str:
    lowered = str(title or "").strip().lower()
    compact = _compact_text(lowered)
    if "microsoft edge" in lowered or "msedge" in compact or "microsoftedge" in compact:
        return "edge"
    if "google chrome" in lowered or compact.endswith("chrome") or "chrome" in lowered:
        return "chrome"
    if "firefox" in lowered:
        return "firefox"
    if "opera" in lowered:
        return "opera"
    return ""


def _title_has_any_token(title: Any, tokens: Sequence[str]) -> bool:
    lowered = str(title or "").strip().lower()
    return any(str(token or "").strip().lower() in lowered for token in tokens if str(token or "").strip())


def _title_matches_window_query(title: Any, query: Any) -> bool:
    if _is_pocket_option_query(query):
        return _is_pocket_option_like_title(title)
    if _title_matches_query(title, query):
        return True
    lowered_title = str(title or "").strip().lower()
    compact_title = _compact_text(lowered_title)
    for token in _window_query_fallback_tokens(query):
        lowered_token = str(token or "").strip().lower()
        compact_token = _compact_text(lowered_token)
        if lowered_token and lowered_token in lowered_title:
            return True
        if compact_token and compact_token in compact_title:
            return True
    return False


def _window_descriptor_is_capture_usable(descriptor: Mapping[str, Any]) -> bool:
    if bool(descriptor.get("is_minimized", False)):
        return False
    try:
        width = int(descriptor.get("width", 0) or 0)
        height = int(descriptor.get("height", 0) or 0)
    except (TypeError, ValueError):
        width = 0
        height = 0
    if width < 600 or height < 360:
        return False
    bbox = cast(Sequence[Any], descriptor.get("bbox", []))
    if len(bbox) >= 4:
        try:
            x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
            if x0 <= -30000 or y0 <= -30000:
                return False
            if x1 - x0 < 600 or y1 - y0 < 360:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _normalize_focus_region_bbox(
    normalized_bbox: Sequence[Any],
    *,
    min_fraction: float = 0.04,
) -> list[float]:
    if len(normalized_bbox) < 4:
        raise ValueError("Focus region must contain four normalized coordinates.")
    left = max(0.0, min(1.0, float(normalized_bbox[0])))
    top = max(0.0, min(1.0, float(normalized_bbox[1])))
    right = max(left + 1e-6, min(1.0, float(normalized_bbox[2])))
    bottom = max(top + 1e-6, min(1.0, float(normalized_bbox[3])))
    if (right - left) < float(min_fraction) or (bottom - top) < float(min_fraction):
        raise ValueError("Focus region is too small. Select a larger chart area.")
    return [left, top, right, bottom]


def normalize_focus_region_bbox(
    normalized_bbox: Sequence[Any],
    *,
    min_fraction: float = 0.04,
) -> list[float]:
    return _normalize_focus_region_bbox(normalized_bbox, min_fraction=min_fraction)


def _public_manual_focus_region(value: Any) -> dict[str, Any]:
    region = _mapping_to_dict(value)
    normalized_bbox: list[float] = []
    raw_bbox = cast(Sequence[Any], region.get("normalized_bbox", []))
    if len(raw_bbox) >= 4:
        try:
            normalized_bbox = _normalize_focus_region_bbox(raw_bbox)
        except ValueError:
            normalized_bbox = []
    enabled = bool(region.get("enabled", False)) and len(normalized_bbox) == 4
    return {
        "enabled": enabled,
        "normalized_bbox": normalized_bbox if enabled else [],
        "source": str(region.get("source", "") or ""),
        "updated_at": str(region.get("updated_at", "") or ""),
    }


def _focus_selector_state(
    *,
    supported: bool,
    armed: bool = False,
    active: bool = False,
    status: str = "idle",
    hotkey: str = "Ctrl+V",
    message: str = "",
    last_error: str = "",
    target_hwnd: int = 0,
    target_title: str = "",
    updated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "supported": bool(supported),
        "armed": bool(armed),
        "active": bool(active),
        "status": str(status or "idle"),
        "hotkey": str(hotkey or "Ctrl+V"),
        "message": str(message or ""),
        "last_error": str(last_error or ""),
        "target_hwnd": int(target_hwnd or 0),
        "target_title": str(target_title or ""),
        "updated_at": str(updated_at or _now_iso()),
    }


def _public_focus_selector_state(value: Any, *, supported: bool) -> dict[str, Any]:
    row = _mapping_to_dict(value)
    return _focus_selector_state(
        supported=bool(row.get("supported", supported)),
        armed=bool(row.get("armed", False)),
        active=bool(row.get("active", False)),
        status=str(row.get("status", "idle") or "idle"),
        hotkey=str(row.get("hotkey", "Ctrl+V") or "Ctrl+V"),
        message=str(row.get("message", "") or ""),
        last_error=str(row.get("last_error", "") or ""),
        target_hwnd=int(row.get("target_hwnd", 0) or 0),
        target_title=str(row.get("target_title", "") or ""),
        updated_at=str(row.get("updated_at", "") or _now_iso()),
    )


def _focus_required_message() -> str:
    return (
        "Arm broker focus, switch to Pocket Option, press Ctrl+V, "
        "drag the chart region, and press Enter to lock it."
    )


def _crop_normalized_bbox(
    image: Image.Image,
    normalized_bbox: Sequence[Any],
) -> tuple[Image.Image, dict[str, Any]]:
    left, top, right, bottom = _normalize_focus_region_bbox(normalized_bbox, min_fraction=0.001)
    x0 = max(0, min(image.width - 1, int(round(image.width * left))))
    y0 = max(0, min(image.height - 1, int(round(image.height * top))))
    x1 = max(x0 + 1, min(image.width, int(round(image.width * right))))
    y1 = max(y0 + 1, min(image.height, int(round(image.height * bottom))))
    return image.crop((x0, y0, x1, y1)), {
        "normalized_bbox": [left, top, right, bottom],
        "pixel_bbox": [x0, y0, x1, y1],
        "width": max(0, x1 - x0),
        "height": max(0, y1 - y0),
    }


def _image_dimensions_payload(image: Image.Image) -> dict[str, int]:
    return {"width": int(image.width), "height": int(image.height)}


def _study_plane_integrity_error(study: TrackingStudy, selected_size: tuple[int, int]) -> str:
    expected = (int(selected_size[0]), int(selected_size[1]))
    chart_size = (int(study.chart_image.width), int(study.chart_image.height))
    overlay_size = (int(study.overlay_image.width), int(study.overlay_image.height))
    if chart_size != expected:
        return (
            "Tracker plane integrity failed: chart artifact dimensions "
            f"{chart_size[0]}x{chart_size[1]} do not match selected broker plane "
            f"{expected[0]}x{expected[1]}."
        )
    if overlay_size != expected:
        return (
            "Tracker plane integrity failed: overlay artifact dimensions "
            f"{overlay_size[0]}x{overlay_size[1]} do not match selected broker plane "
            f"{expected[0]}x{expected[1]}."
        )
    return ""


def _pixel_bbox_meta(image_size: tuple[int, int], bbox: Sequence[Any]) -> dict[str, Any]:
    width = max(1, int(image_size[0]))
    height = max(1, int(image_size[1]))
    x0 = max(0, min(width - 1, int(round(float(bbox[0])))))
    y0 = max(0, min(height - 1, int(round(float(bbox[1])))))
    x1 = max(x0 + 1, min(width, int(round(float(bbox[2])))))
    y1 = max(y0 + 1, min(height, int(round(float(bbox[3])))))
    return {
        "pixel_bbox": [x0, y0, x1, y1],
        "normalized_bbox": [
            float(x0 / width),
            float(y0 / height),
            float(x1 / width),
            float(y1 / height),
        ],
        "width": int(x1 - x0),
        "height": int(y1 - y0),
    }


def _binary_content_bbox(mask: ArrayND) -> tuple[int, int, int, int] | None:
    if mask.ndim != 2:
        return None
    ys, xs = np.where(mask > 0)
    if ys.size == 0 or xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


@lru_cache(maxsize=96)
def _expiry_text_template_bank() -> tuple[tuple[str, int, ArrayND], ...]:
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception:
        return tuple()

    candidate_seconds = sorted(
        set(
            [3, 15, 30]
            + [minute * 60 for minute in range(1, 60)]
            + [hour * 3600 for hour in range(1, 25)]
        )
    )
    fonts = (
        cv2.FONT_HERSHEY_SIMPLEX,
        cv2.FONT_HERSHEY_DUPLEX,
        cv2.FONT_HERSHEY_COMPLEX_SMALL,
    )
    font_scales = (0.42, 0.50, 0.58, 0.66)
    thicknesses = (1, 2)
    templates: list[tuple[str, int, ArrayND]] = []
    for seconds in candidate_seconds:
        hours, remainder = divmod(int(seconds), 3600)
        minutes, second_value = divmod(remainder, 60)
        label = f"{hours:02d}:{minutes:02d}:{second_value:02d}"
        for font in fonts:
            for font_scale in font_scales:
                for thickness in thicknesses:
                    (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
                    canvas = np.zeros(
                        (
                            max(18, text_height + baseline + 8),
                            max(78, text_width + 12),
                        ),
                        dtype=np.uint8,
                    )
                    origin = (
                        max(4, (canvas.shape[1] - text_width) // 2),
                        max(text_height + 3, (canvas.shape[0] + text_height) // 2 - baseline // 2),
                    )
                    cv2.putText(canvas, label, origin, font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
                    bbox = _binary_content_bbox(canvas)
                    if bbox is None:
                        continue
                    tx0, ty0, tx1, ty1 = bbox
                    cropped = canvas[max(0, ty0 - 1): min(canvas.shape[0], ty1 + 1), max(0, tx0 - 1): min(canvas.shape[1], tx1 + 1)]
                    if cropped.size:
                        templates.append((label, int(seconds), (cropped > 0).astype(np.uint8)))
    return tuple(templates)


def _read_expiry_text_by_template(mask: ArrayND) -> dict[str, Any]:
    try:
        import cv2  # type: ignore[import-not-found]
    except Exception:
        return {}
    bbox = _binary_content_bbox(mask)
    if bbox is None:
        return {}
    x0, y0, x1, y1 = bbox
    content = (mask[max(0, y0 - 1): y1 + 1, max(0, x0 - 1): x1 + 1] > 0).astype(np.uint8)
    if content.size == 0 or int(np.sum(content > 0)) < 18:
        return {}
    best_label = ""
    best_seconds = 0
    best_score = 0.0
    second_best = 0.0
    for label, seconds, template in _expiry_text_template_bank():
        if template.size == 0:
            continue
        resized = cv2.resize(
            content,
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
        score = 0.62 * harmonic + 0.38 * iou
        if score > best_score:
            second_best = best_score
            best_label = label
            best_seconds = int(seconds)
            best_score = score
        elif score > second_best:
            second_best = score
    margin = best_score - second_best
    if best_label and best_score >= 0.28 and margin >= -0.015:
        return {
            "text": best_label,
            "seconds": int(best_seconds),
            "confidence": _clip01(best_score),
            "source": "time_field_template_ocr",
        }
    return {}


def _clip_bbox_to_image(image_size: tuple[int, int], bbox: Sequence[Any]) -> list[int]:
    width, height = image_size
    x0 = max(0, min(width - 1, int(round(float(bbox[0])))))
    y0 = max(0, min(height - 1, int(round(float(bbox[1])))))
    x1 = max(x0 + 1, min(width, int(round(float(bbox[2])))))
    y1 = max(y0 + 1, min(height, int(round(float(bbox[3])))))
    return [x0, y0, x1, y1]


def _clip_bbox_to_bounds(bounds: Sequence[Any], bbox: Sequence[Any]) -> list[int]:
    left, top, right, bottom = [int(round(float(value))) for value in bounds[:4]]
    if right <= left:
        right = left + 1
    if bottom <= top:
        bottom = top + 1
    x0 = max(left, min(right - 1, int(round(float(bbox[0])))))
    y0 = max(top, min(bottom - 1, int(round(float(bbox[1])))))
    x1 = max(x0 + 1, min(right, int(round(float(bbox[2])))))
    y1 = max(y0 + 1, min(bottom, int(round(float(bbox[3])))))
    return [x0, y0, x1, y1]


def _clip_point_to_bounds(bounds: Sequence[Any], point: Sequence[Any], *, pad: int = 0) -> tuple[int, int]:
    left, top, right, bottom = [int(round(float(value))) for value in bounds[:4]]
    inner_left = min(right - 1, left + max(0, int(pad)))
    inner_top = min(bottom - 1, top + max(0, int(pad)))
    inner_right = max(inner_left + 1, right - max(0, int(pad)))
    inner_bottom = max(inner_top + 1, bottom - max(0, int(pad)))
    x = max(inner_left, min(inner_right, int(round(float(point[0])))))
    y = max(inner_top, min(inner_bottom, int(round(float(point[1])))))
    return x, y


def _expand_bbox(
    image_size: tuple[int, int],
    bbox: Sequence[Any],
    *,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
) -> list[int]:
    x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
    expanded = [x0 - int(round(pad_x)), y0 - int(round(pad_y)), x1 + int(round(pad_x)), y1 + int(round(pad_y))]
    return _clip_bbox_to_image(image_size, expanded)


def _translate_bbox(bbox: Sequence[Any], *, offset_x: float = 0.0, offset_y: float = 0.0) -> list[int]:
    return [
        int(round(float(bbox[0]) + offset_x)),
        int(round(float(bbox[1]) + offset_y)),
        int(round(float(bbox[2]) + offset_x)),
        int(round(float(bbox[3]) + offset_y)),
    ]


def _rgba(color: ColorRGB, alpha: int) -> ColorRGBA:
    return (int(color[0]), int(color[1]), int(color[2]), int(alpha))


@lru_cache(maxsize=32)
def _overlay_font(size: int, *, bold: bool = False) -> OverlayFont:
    requested = max(10, int(size))
    candidates = (
        (
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
        if bold
        else (
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
    )
    for raw_path in candidates:
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), requested)
        except OSError:
            continue
    return ImageFont.load_default()


def _encode_png(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


def _compose_full_window_overlay(
    window_image: Image.Image,
    selected_overlay: Image.Image,
    focus_meta: Mapping[str, Any],
) -> Image.Image:
    base = window_image.convert("RGBA")
    overlay = selected_overlay.convert("RGBA")
    bbox = cast(Sequence[Any], focus_meta.get("pixel_bbox", []))
    if len(bbox) < 4:
        return base.convert("RGB")
    x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
    x0 = max(0, min(base.width - 1, x0))
    y0 = max(0, min(base.height - 1, y0))
    x1 = max(x0 + 1, min(base.width, x1))
    y1 = max(y0 + 1, min(base.height, y1))
    target_size = (max(1, x1 - x0), max(1, y1 - y0))
    if overlay.size != target_size:
        overlay = overlay.resize(target_size, Image.Resampling.BILINEAR)
    canvas = base.copy()
    canvas.alpha_composite(overlay, (x0, y0))
    return canvas.convert("RGB")


def _surface_signature(image: Image.Image) -> str:
    arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    sample = arr[:: max(1, arr.shape[0] // 48), :: max(1, arr.shape[1] // 48), :]
    digest = np.asarray(sample, dtype=np.uint8).tobytes()
    return uuid4().hex[:6] if not digest else f"{abs(hash(digest)) & 0xFFFFFFFF:08x}"


def _group_column_runs(active: ArrayND, *, max_gap: int = 2) -> list[tuple[int, int]]:
    indices = np.flatnonzero(active > 0)
    if indices.size == 0:
        return []
    groups: list[tuple[int, int]] = []
    start = int(indices[0])
    end = int(indices[0])
    for raw_idx in indices[1:]:
        idx = int(raw_idx)
        if idx <= (end + max(1, int(max_gap))):
            end = idx
            continue
        groups.append((start, end + 1))
        start = idx
        end = idx
    groups.append((start, end + 1))
    return groups


def _trend_direction(value: float, *, epsilon: float = 0.012) -> str:
    if value > epsilon:
        return "BUY"
    if value < -epsilon:
        return "SELL"
    return "HOLD"


def _regression_slope(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    xs = np.linspace(0.0, 1.0, num=len(values), dtype=np.float32)
    ys = np.asarray(values, dtype=np.float32)
    if float(np.std(ys)) <= 1e-9:
        return 0.0
    slope, _intercept = np.polyfit(xs, ys, 1)
    return float(slope)


def _study_entry(signal: Mapping[str, Any], tracking: Mapping[str, Any]) -> dict[str, Any]:
    decision_kernel = _mapping_to_dict(signal.get("decision_kernel", {}))
    countertrend_lane = _mapping_to_dict(signal.get("countertrend_lane", {}))
    broker_execution = _mapping_to_dict(signal.get("broker_execution_state", tracking.get("broker_execution_state", {})))
    return {
        "captured_at": _now_iso(),
        "action": str(signal.get("action", "HOLD") or "HOLD"),
        "execution_action": str(signal.get("execution_action", "HOLD") or "HOLD"),
        "entry_state": str(signal.get("entry_state", "WAIT") or "WAIT"),
        "setup": str(signal.get("setup", "") or ""),
        "confidence": _clip01(signal.get("effective_confidence", 0.0)),
        "summary": str(signal.get("summary", "") or ""),
        "timeframe": str(signal.get("focus_timeframe", "") or ""),
        "market": str(signal.get("market", "") or ""),
        "global_direction": str(tracking.get("global_direction", "HOLD") or "HOLD"),
        "local_direction": str(tracking.get("local_direction", "HOLD") or "HOLD"),
        "dominant_side": str(decision_kernel.get("dominant_side", "hold") or "hold").upper(),
        "kernel_state": str(decision_kernel.get("state", "") or ""),
        "kernel_decision": str(decision_kernel.get("decision", "") or ""),
        "setup_age_candles": int(decision_kernel.get("setup_age_candles", 0) or 0),
        "candles_to_trigger": int(decision_kernel.get("eta_trigger_candles", 0) or 0),
        "candles_to_target": int(decision_kernel.get("eta_target_after_trigger_candles", 0) or 0),
        "candles_to_invalidation": int(decision_kernel.get("eta_invalidation_candles", 0) or 0),
        "candles_to_stale": int(decision_kernel.get("stale_after_candles", 0) or 0),
        "triggered": str(decision_kernel.get("state", "") or "").upper() in {"TRIGGERED", "ACTIVE", "COMPLETE"},
        "target_before_invalidation": str(decision_kernel.get("next_most_likely_event", "") or "") == "target",
        "next_candle_bias": str(decision_kernel.get("next_candle_bias", "hold") or "hold").upper(),
        "trade_mode": str(decision_kernel.get("trade_mode", "") or ""),
        "candle_execution_side": str(decision_kernel.get("candle_execution_side", "hold") or "hold").upper(),
        "hold_for_candles": int(decision_kernel.get("hold_for_candles", 0) or 0),
        "countertrend_state": str(countertrend_lane.get("state", "INACTIVE") or "INACTIVE"),
        "countertrend_side": str(countertrend_lane.get("side", "HOLD") or "HOLD").upper(),
        "countertrend_actionable": bool(countertrend_lane.get("actionable", False)),
        "broker_execution_status": str(broker_execution.get("status", "") or ""),
        "broker_execution_message": str(broker_execution.get("message", "") or ""),
        "phoenixguard_decision_state": str(
            signal.get(
                "phoenixguard_decision_state",
                _mapping_to_dict(tracking.get("phoenixguard_report", {})).get("decision_state", "forming"),
            )
            or "forming"
        ),
        "phoenixguard_report_summary": str(signal.get("phoenixguard_report_summary", "") or ""),
    }


def _tracker_signal_state_hash(
    latest_signal: Mapping[str, Any],
    tracking_summary: Mapping[str, Any],
    decision_kernel: Mapping[str, Any] | None = None,
) -> str:
    kernel = _mapping_to_dict(
        decision_kernel
        if decision_kernel is not None
        else latest_signal.get("decision_kernel", tracking_summary.get("decision_kernel", {}))
    )
    global_local_control = _mapping_to_dict(
        latest_signal.get("global_local_control", tracking_summary.get("global_local_control", {}))
    )
    countertrend_lane = _mapping_to_dict(
        latest_signal.get("countertrend_lane", tracking_summary.get("countertrend_lane", {}))
    )
    execution_side = _upper_action(latest_signal.get("execution_action", latest_signal.get("action", "HOLD")))
    payload = {
        "action": _upper_action(latest_signal.get("action", "HOLD")),
        "candidate_action": _upper_action(latest_signal.get("candidate_action", latest_signal.get("action", "HOLD"))),
        "execution_action": execution_side,
        "actionable": bool(latest_signal.get("actionable", False)) and execution_side in {"BUY", "SELL"},
        "entry_state": str(
            latest_signal.get("entry_state", tracking_summary.get("entry_state", "WAIT"))
            or "WAIT"
        ).upper(),
        "entry_label": str(
            latest_signal.get("entry_label", tracking_summary.get("entry_label", "WAIT"))
            or "WAIT"
        ).upper(),
        "kernel_state": str(kernel.get("state", "") or "").upper(),
        "kernel_decision": str(kernel.get("decision", "") or "").upper(),
        "dominant_side": _upper_action(kernel.get("dominant_side", latest_signal.get("dominant_side", "HOLD"))),
        "next_candle_bias": _upper_action(kernel.get("next_candle_bias", "HOLD")),
        "candle_execution_side": _upper_action(kernel.get("candle_execution_side", "HOLD")),
        "countertrend_actionable": bool(countertrend_lane.get("actionable", False)),
        "countertrend_side": _upper_action(countertrend_lane.get("side", "HOLD")),
        "global_direction": _upper_action(tracking_summary.get("global_direction", "HOLD")),
        "local_direction": _upper_action(tracking_summary.get("local_direction", "HOLD")),
        "impulse_direction": _upper_action(tracking_summary.get("impulse_direction", "HOLD")),
        "control_direction": _upper_action(global_local_control.get("direction", "HOLD")),
        "control_owner": str(global_local_control.get("owner", "") or "").lower(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _default_signal(*, message: str, status: str = "idle") -> dict[str, Any]:
    return {
        "action": "HOLD",
        "headline_action": "HOLD",
        "candidate_action": "HOLD",
        "model_action": "HOLD",
        "execution_action": "HOLD",
        "execution_confidence": 0.0,
        "confidence": 0.0,
        "effective_confidence": 0.0,
        "candidate_confidence": 0.0,
        "raw_confidence": 0.0,
        "status": str(status or "idle"),
        "summary": str(message or ""),
        "setup": "",
        "focus_timeframe": "",
        "focus_timeframe_source": "unconfirmed",
        "market": "",
        "market_source": "unconfirmed",
        "market_confidence": 0.0,
        "execution_permission": "WAIT",
        "entry_state": "WAIT",
        "entry_label": "WAIT",
        "entry_stage_label": "WAIT",
        "entry_quality": "NONE",
        "actionable": False,
        "countertrend_lane": {
            "state": "INACTIVE",
            "side": "HOLD",
            "trade_mode": "STAND_ASIDE",
            "window_candles": 0,
            "actionable": False,
            "instruction": "",
        },
        "timing_signal": {
            "entry_state": "WATCH",
            "timing_score": 0.0,
            "entry_quality": "NONE",
            "instruction": str(message or ""),
        },
        "probability": {
            "target_first_probability": 0.0,
            "invalidation_first_probability": 0.0,
            "sideways_probability": 1.0,
            "expected_candles_to_resolution": [0, 0],
            "sample_weight": 0.0,
            "probability_state": "NO_EDGE",
        },
        "overlay_instructions": [
            "SNIPER WATCH is an early area to watch, not automatic execution.",
            "TRIGGER READY or SNIPER READY is the executable state.",
            "INVALIDATED cancels the idea; it is not a buy or sell entry.",
        ],
        "reasons": [str(message or "")] if message else [],
        "timestamp": _now_iso(),
        "phoenixguard_decision_state": "forming",
        "phoenixguard_report_summary": str(message or ""),
        "phoenixguard_report_status": str(status or "idle"),
    }


def _default_broker_surface_payload(*, message: str = "Broker controls have not been read yet.") -> dict[str, Any]:
    return {
        "state": "unknown",
        "message": str(message or ""),
        "read_at": "",
        "capture_plane": {
            "source": "unread",
            "width": 0,
            "height": 0,
            "uses_manual_focus_crop": False,
            "manual_focus_bbox": [],
            "message": "Broker controls have not been read from a GUI capture yet.",
        },
        "controls_ready": False,
        "order_panel": {},
        "buy_button": {},
        "sell_button": {},
        "amount_field": {},
        "time_field": {},
        "execution_boxes": {},
        "control_visibility": {
            "image_width": 0,
            "image_height": 0,
            "buy_visible": False,
            "sell_visible": False,
            "amount_visible": False,
            "time_visible": False,
            "all_required_visible": False,
            "message": "Full broker window controls have not been visibility-checked yet.",
        },
        "expiry_lock": {
            "required_seconds": _EXECUTION_DEFAULT_EXPIRY_SEC,
            "configured_seconds": 0,
            "configured_text": "",
            "field_ready": False,
            "confidence": 0.0,
            "visible_text": "",
            "visible_seconds": 0,
            "visible_confidence": 0.0,
            "message": "Expiry/time field has not been read yet.",
        },
        "detected_market": "",
        "market_source": "unconfirmed",
        "market_confidence": 0.0,
        "detected_timeframe": "",
        "timeframe_source": "unconfirmed",
        "timeframe_confidence": 0.0,
        "identity_ready": False,
        "identity_message": "Broker market/timeframe identity has not been read yet.",
        "amount_lock": {
            "required": _FIXED_BROKER_AMOUNT,
            "configured": _FIXED_BROKER_AMOUNT,
            "verified": False,
            "confidence": 0.0,
            "message": "Amount is fixed to $5; live execution must set/verify it before every click.",
        },
        "confidence": 0.0,
    }


def _default_execution_controls() -> dict[str, Any]:
    return {
        "live_execution_enabled": False,
        "execution_mode": "shadow",
        "fixed_amount": _FIXED_BROKER_AMOUNT,
        "allow_countertrend_scalp": False,
        "scenario_generation_enabled": False,
        "auto_memory_projection": True,
        "require_memory_projection": True,
        "require_market_identity": True,
        "require_timeframe_identity": False,
        "adaptive_timer_enabled": True,
        "min_capture_interval_sec": _EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC,
        "max_capture_interval_sec": _EXECUTION_DEFAULT_MAX_CAPTURE_INTERVAL_SEC,
        "max_executions_per_window": 5,
        "execution_window_sec": 300,
        "min_market_confidence": 0.42,
        "min_timeframe_confidence": 0.42,
        "cooldown_sec": _EXECUTION_DEFAULT_COOLDOWN_SEC,
        "max_active_trades": 1,
    }


def _normalize_execution_controls(value: Any) -> dict[str, Any]:
    raw = _mapping_to_dict(value)
    controls = _default_execution_controls()
    controls.update(raw)
    mode = str(controls.get("execution_mode", "shadow") or "shadow").strip().lower()
    controls["execution_mode"] = "live" if mode == "live" else "shadow"
    controls["fixed_amount"] = _FIXED_BROKER_AMOUNT
    controls["live_execution_enabled"] = bool(controls.get("live_execution_enabled", False))
    controls["allow_countertrend_scalp"] = bool(controls.get("allow_countertrend_scalp", False))
    controls["scenario_generation_enabled"] = bool(controls.get("scenario_generation_enabled", False))
    controls["auto_memory_projection"] = bool(controls.get("auto_memory_projection", True))
    controls["require_memory_projection"] = bool(controls.get("require_memory_projection", True))
    controls["require_market_identity"] = bool(controls.get("require_market_identity", True))
    controls["require_timeframe_identity"] = bool(controls.get("require_timeframe_identity", False))
    controls["adaptive_timer_enabled"] = bool(controls.get("adaptive_timer_enabled", True))
    min_interval = max(_EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC, float(controls.get("min_capture_interval_sec", _EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC) or _EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC))
    max_interval = max(min_interval, float(controls.get("max_capture_interval_sec", _EXECUTION_DEFAULT_MAX_CAPTURE_INTERVAL_SEC) or _EXECUTION_DEFAULT_MAX_CAPTURE_INTERVAL_SEC))
    controls["min_capture_interval_sec"] = min_interval
    controls["max_capture_interval_sec"] = max_interval
    controls["max_executions_per_window"] = max(1, int(controls.get("max_executions_per_window", 5) or 5))
    controls["execution_window_sec"] = max(60.0, float(controls.get("execution_window_sec", 300) or 300))
    controls["min_market_confidence"] = _clip01(controls.get("min_market_confidence", 0.42))
    controls["min_timeframe_confidence"] = _clip01(controls.get("min_timeframe_confidence", 0.42))
    controls["cooldown_sec"] = max(5.0, float(controls.get("cooldown_sec", _EXECUTION_DEFAULT_COOLDOWN_SEC) or _EXECUTION_DEFAULT_COOLDOWN_SEC))
    controls["max_active_trades"] = 1
    return controls


def _default_broker_execution_state(
    *,
    status: str = "disabled",
    message: str = "Live execution is disabled.",
) -> dict[str, Any]:
    return {
        "status": str(status or "disabled"),
        "message": str(message or ""),
        "mode": "shadow",
        "enabled": False,
        "side": "HOLD",
        "lane": "NONE",
        "actionable": False,
        "amount": _FIXED_BROKER_AMOUNT,
        "expiry_seconds": _EXECUTION_DEFAULT_EXPIRY_SEC,
        "memory_gate": "not_checked",
        "memory_projection": {},
        "broker_surface": _default_broker_surface_payload(),
        "last_attempt_at": "",
        "last_attempt_epoch": 0.0,
        "last_trade_at": "",
        "last_trade_epoch": 0.0,
        "cooldown_until": "",
        "cooldown_until_epoch": 0.0,
        "retry_block_until": "",
        "retry_block_until_epoch": 0.0,
        "active_trade": {},
        "last_result": {},
        "throttle": {
            "window_started_epoch": 0.0,
            "window_started_at": "",
            "executions_in_window": 0,
            "max_executions": 5,
            "window_seconds": 300.0,
            "blocked_until_epoch": 0.0,
            "blocked_until": "",
            "message": "Execution throttle allows up to 5 clicks per 5-minute window.",
        },
        "recent_log": [],
    }

def _normalize_broker_execution_state(value: Any) -> dict[str, Any]:
    raw = _mapping_to_dict(value)
    state = _default_broker_execution_state()
    state.update(raw)
    state["status"] = str(state.get("status", "disabled") or "disabled")
    state["message"] = str(state.get("message", "") or "")
    state["mode"] = "live" if str(state.get("mode", "shadow") or "shadow").lower() == "live" else "shadow"
    state["enabled"] = bool(state.get("enabled", False))
    state["side"] = _upper_action(state.get("side", "HOLD"))
    state["lane"] = str(state.get("lane", "NONE") or "NONE").upper()
    state["actionable"] = bool(state.get("actionable", False))
    state["amount"] = _FIXED_BROKER_AMOUNT
    state["expiry_seconds"] = max(1, int(state.get("expiry_seconds", _EXECUTION_DEFAULT_EXPIRY_SEC) or _EXECUTION_DEFAULT_EXPIRY_SEC))
    state["memory_projection"] = _mapping_to_dict(state.get("memory_projection", {}))
    state["broker_surface"] = dict(_default_broker_surface_payload(), **_mapping_to_dict(state.get("broker_surface", {})))
    state["active_trade"] = _mapping_to_dict(state.get("active_trade", {}))
    state["last_result"] = _mapping_to_dict(state.get("last_result", {}))
    active_trade = _mapping_to_dict(state.get("active_trade", {}))
    if active_trade:
        expires_epoch = float(active_trade.get("expires_epoch", 0.0) or 0.0)
        if expires_epoch <= 0.0:
            opened_epoch = float(active_trade.get("opened_epoch", 0.0) or 0.0)
            trade_expiry = int(active_trade.get("expiry_seconds", state.get("expiry_seconds", 0)) or 0)
            if opened_epoch > 0.0 and trade_expiry > 0:
                expires_epoch = opened_epoch + float(trade_expiry)
        if expires_epoch > 0.0 and expires_epoch <= _now_epoch():
            expired_message = "Previous trade window expired; broker outcome was not visually certified."
            if str(active_trade.get("lane", "") or "").upper() == "DEMO_RANDOM_TEST":
                expired_message = "Previous demo trade window expired; broker outcome was not visually certified."
            state["last_result"] = {
                "status": "expired_unverified",
                "message": expired_message,
                "resolved_at": _now_iso(),
                "trade": active_trade,
            }
            state["active_trade"] = {}
            if str(state.get("status", "") or "").lower() in {"clicked", "monitoring"}:
                state["status"] = "expired_unverified"
                state["message"] = expired_message
    state["last_attempt_epoch"] = max(0.0, float(state.get("last_attempt_epoch", 0.0) or 0.0))
    state["last_trade_epoch"] = max(0.0, float(state.get("last_trade_epoch", 0.0) or 0.0))
    state["cooldown_until_epoch"] = max(0.0, float(state.get("cooldown_until_epoch", 0.0) or 0.0))
    retry_until = max(0.0, float(state.get("retry_block_until_epoch", 0.0) or 0.0))
    if retry_until <= _now_epoch():
        retry_until = 0.0
        state["retry_block_until"] = ""
    state["retry_block_until_epoch"] = retry_until
    state["throttle"] = _mapping_to_dict(state.get("throttle", {})) or _default_broker_execution_state()["throttle"]
    state["recent_log"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], state.get("recent_log", []))
        if isinstance(item, Mapping)
    ][:20]
    return state


def _preserve_newer_active_execution_state(
    candidate: Mapping[str, Any],
    persisted: Mapping[str, Any],
) -> dict[str, Any]:
    merged = _normalize_broker_execution_state(candidate)
    previous = _normalize_broker_execution_state(persisted)
    previous_result = _mapping_to_dict(previous.get("last_result", {}))
    merged_result = _mapping_to_dict(merged.get("last_result", {}))
    previous_attempt_epoch = float(previous.get("last_attempt_epoch", 0.0) or 0.0)
    merged_attempt_epoch = float(merged.get("last_attempt_epoch", 0.0) or 0.0)
    passive_statuses = {"watching", "disabled", "shadow_ready", "armed", "monitoring"}
    previous_retry_until = float(previous.get("retry_block_until_epoch", 0.0) or 0.0)
    if previous_result and (
        not merged_result
        or previous_attempt_epoch >= merged_attempt_epoch
        or str(merged.get("status", "") or "").lower() in passive_statuses
    ):
        merged["last_result"] = previous_result
        merged["retry_block_until_epoch"] = previous_retry_until
        merged["retry_block_until"] = str(previous.get("retry_block_until", "") or "")
        merged["recent_log"] = list(cast(Sequence[Any], previous.get("recent_log", merged.get("recent_log", []))))[:20]
        if previous_retry_until > _now_epoch() and str(merged.get("status", "") or "").lower() in passive_statuses:
            merged["status"] = "retry_wait"
            merged["message"] = _execution_retry_backoff_message(merged, _now_epoch())
    merged_trade = _mapping_to_dict(merged.get("active_trade", {}))
    previous_trade = _mapping_to_dict(previous.get("active_trade", {}))
    if not previous_trade:
        return merged
    previous_expires = float(previous_trade.get("expires_epoch", 0.0) or 0.0)
    if previous_expires <= _now_epoch():
        return merged
    merged_opened = float(merged_trade.get("opened_epoch", 0.0) or 0.0)
    previous_opened = float(previous_trade.get("opened_epoch", 0.0) or 0.0)
    if merged_trade and merged_opened > previous_opened:
        return merged
    merged["active_trade"] = previous_trade
    merged["last_trade_at"] = str(previous.get("last_trade_at", "") or merged.get("last_trade_at", ""))
    merged["last_trade_epoch"] = float(previous.get("last_trade_epoch", 0.0) or 0.0)
    merged["cooldown_until_epoch"] = float(previous.get("cooldown_until_epoch", 0.0) or 0.0)
    merged["cooldown_until"] = str(previous.get("cooldown_until", "") or "")
    merged["last_result"] = _mapping_to_dict(previous.get("last_result", merged.get("last_result", {})))
    merged["throttle"] = _mapping_to_dict(previous.get("throttle", merged.get("throttle", {})))
    if str(merged.get("status", "") or "").lower() in {"watching", "disabled", "shadow_ready", "armed"}:
        merged["status"] = "monitoring"
        merged["message"] = f"Existing {previous_trade.get('side', 'trade')} trade is still being monitored."
    return merged


def _same_execution_retry_target(
    last_result: Mapping[str, Any],
    *,
    side: str,
    lane: str,
    expiry_seconds: int,
) -> bool:
    result_side = _upper_action(last_result.get("side", "HOLD"))
    result_lane = str(last_result.get("lane", "") or "").upper()
    result_expiry = int(last_result.get("expiry_seconds", 0) or 0)
    return (
        result_side == _upper_action(side)
        and result_expiry == int(expiry_seconds)
        and result_lane in {"", str(lane or "").upper()}
    )


def _arm_execution_retry_backoff(
    state: dict[str, Any],
    *,
    now_epoch: float,
    side: str,
    lane: str,
    expiry_seconds: int,
    result: Mapping[str, Any],
    backoff_sec: float = _EXECUTION_FAILED_ATTEMPT_BACKOFF_SEC,
) -> None:
    retry_until = float(now_epoch) + max(15.0, float(backoff_sec or _EXECUTION_FAILED_ATTEMPT_BACKOFF_SEC))
    state["retry_block_until_epoch"] = retry_until
    state["retry_block_until"] = _epoch_to_utc_iso(retry_until)
    enriched = dict(result)
    enriched["side"] = _upper_action(enriched.get("side", side))
    enriched["lane"] = str(enriched.get("lane", lane) or lane)
    enriched["expiry_seconds"] = int(enriched.get("expiry_seconds", expiry_seconds) or expiry_seconds)
    enriched["attempted_at"] = _now_iso()
    enriched["retry_block_until"] = state["retry_block_until"]
    state["last_result"] = enriched


def _execution_retry_backoff_message(state: Mapping[str, Any], now_epoch: float) -> str:
    remaining = max(0.0, float(state.get("retry_block_until_epoch", 0.0) or 0.0) - float(now_epoch))
    last_result = _mapping_to_dict(state.get("last_result", {}))
    reason = str(last_result.get("message", "") or "previous broker execution attempt did not complete")
    return f"Execution retry is cooling down for {remaining:.1f}s after {reason}."


def _default_phoenixguard_report(*, message: str = "", status: str = "warming") -> dict[str, Any]:
    note = str(message or "PhoenixGuard report is warming.")
    return {
        "status": str(status or "warming"),
        "headline": note,
        "generated_at": _now_iso(),
        "memory_findings": {
            "bank_ready": False,
            "total_entries": 0,
            "buy_count": 0,
            "sell_count": 0,
            "buys": note,
            "sells": note,
            "reversals": note,
            "pullbacks": note,
            "continuations": note,
            "wick_behavior": note,
            "sequence_behavior": note,
            "early_entries": note,
            "late_entries": note,
            "notes": [note] if note else [],
        },
        "current_market_structure": {
            "global_structure": note,
            "major_local_structure": note,
            "nested_local_structure": note,
            "microstructure": note,
            "latest_candle_state": note,
            "current_active_transition_state": note,
            "state_stack": "",
            "active_zones": {},
        },
        "memory_to_current_match": {
            "dominant_memory_side": "HOLD",
            "top_matches": [],
            "how_current_differs": note,
            "historical_next_event": note,
            "historical_next_event_bias": {},
        },
        "decision_state": "forming",
        "forward_projection": {
            "dominant_side": "HOLD",
            "expected_next_move": note,
            "likely_path": note,
            "likely_reaction_points": [],
            "likely_trigger_area": "",
            "likely_target_area": "",
            "likely_invalidation_area": "",
            "immediacy": "forming",
            "opportunity_timing": "early",
        },
        "timing_judgment": {
            "actionable_now": False,
            "must_happen_first": note,
            "high_quality_conditions": note,
            "weak_or_invalid_conditions": note,
            "projection_timing": "still_forming",
            "still_on_time": True,
        },
        "tracker_upgrade_guidance": {
            "state_stack": "",
            "overlay_priority": [],
            "memory_panel": [],
            "alert_conditions": [],
            "forward_projection_contract": note,
        },
    }


def _memory_projection_default_note(mode: str) -> str:
    normalized_mode = str(mode or "predict").strip().lower()
    if normalized_mode == "future":
        return "Run Show Future on the latest locked chart to load the memory-backed future path."
    return "Run Predict on the latest locked chart to load memory-matched projection."


def _default_memory_projection_payload(
    *,
    mode: str,
    message: str = "",
    status: str = "idle",
) -> dict[str, Any]:
    normalized_mode = "future" if str(mode or "").strip().lower() == "future" else "predict"
    note = str(message or _memory_projection_default_note(normalized_mode))
    return {
        "mode": normalized_mode,
        "status": str(status or "idle"),
        "summary": note,
        "generated_at": "",
        "source_frame_index": -1,
        "source_chart_path": "",
        "reference_image_path": "",
        "reference_image_name": "",
        "projection_image_path": "",
        "is_current": False,
        "timeframe": "",
        "market": "",
        "dominant_side": "HOLD",
        "counter_side": "HOLD",
        "decision_state": "forming",
        "trade_bias": "HOLD",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "actionable": False,
        "memory_similarity": 0.0,
        "memory_precision_score": 0.0,
        "memory_edge": 0.0,
        "memory_retrieval": {
            "state": str(status or "idle"),
            "message": note,
            "bank_loaded": False,
            "entries": 0,
            "started_at": "",
            "completed_at": "",
        },
        "memory_precision": {
            "accepted": False,
            "quality": "idle",
            "primary_similarity": 0.0,
            "primary_precision": 0.0,
            "counter_similarity": 0.0,
            "counter_precision": 0.0,
            "edge": 0.0,
            "precision_edge": 0.0,
            "minimum_similarity": _MEMORY_PRECISION_MIN_SIMILARITY,
            "minimum_precision": _MEMORY_PRECISION_MIN_SCORE,
            "minimum_edge": _MEMORY_PRECISION_MIN_EDGE,
            "reason": note,
        },
        "memory_direction": "HOLD",
        "model_council": {},
        "prediction_stack": [],
        "primary_fit": {
            "summary": note,
            "top_matches": [],
            "top_predictions": [],
            "transition_bias": {},
            "candle_templates": [],
        },
        "counter_fit": {
            "summary": note,
            "top_matches": [],
            "top_predictions": [],
            "transition_bias": {},
            "candle_templates": [],
        },
        "forward_projection": {
            "headline": note,
            "path": note,
            "trigger_area": "",
            "target_area": "",
            "invalidation_area": "",
            "immediacy": "forming",
            "projected_candles": [],
            "path_steps": [],
        },
        "hotspots": [],
    }


def _normalize_memory_projection_payload(
    value: Any,
    *,
    mode: str,
    message: str = "",
) -> dict[str, Any]:
    base = _default_memory_projection_payload(mode=mode, message=message)
    raw = _mapping_to_dict(value)
    payload = dict(base)
    payload.update(raw)
    payload["mode"] = str(base["mode"])
    payload["status"] = str(raw.get("status", base["status"]) or base["status"])
    payload["summary"] = str(raw.get("summary", base["summary"]) or base["summary"])
    payload["generated_at"] = str(raw.get("generated_at", "") or "")
    payload["source_frame_index"] = int(raw.get("source_frame_index", -1) or -1)
    payload["source_chart_path"] = str(raw.get("source_chart_path", "") or "")
    payload["reference_image_path"] = str(raw.get("reference_image_path", "") or "")
    payload["reference_image_name"] = str(raw.get("reference_image_name", "") or "")
    payload["projection_image_path"] = str(raw.get("projection_image_path", "") or "")
    payload["is_current"] = bool(raw.get("is_current", False))
    payload["timeframe"] = str(raw.get("timeframe", "") or "")
    payload["market"] = str(raw.get("market", "") or "")
    payload["dominant_side"] = _upper_action(raw.get("dominant_side", "HOLD"))
    payload["counter_side"] = _upper_action(raw.get("counter_side", "HOLD"))
    payload["decision_state"] = str(raw.get("decision_state", base["decision_state"]) or base["decision_state"])
    payload["trade_bias"] = _upper_action(raw.get("trade_bias", "HOLD"))
    payload["execution_permission"] = str(
        raw.get("execution_permission", base["execution_permission"]) or base["execution_permission"]
    ).upper()
    payload["actionable"] = bool(raw.get("actionable", False))
    payload["memory_similarity"] = _clip01(raw.get("memory_similarity", 0.0))
    payload["memory_precision_score"] = _clip01(raw.get("memory_precision_score", 0.0))
    payload["memory_edge"] = round(_float_or(raw.get("memory_edge", 0.0), 0.0), 4)
    memory_retrieval = dict(cast(dict[str, Any], base["memory_retrieval"]))
    memory_retrieval.update(_mapping_to_dict(raw.get("memory_retrieval", {})))
    memory_retrieval["state"] = str(memory_retrieval.get("state", payload["status"]) or payload["status"])
    memory_retrieval["message"] = str(memory_retrieval.get("message", payload["summary"]) or payload["summary"])
    memory_retrieval["bank_loaded"] = bool(memory_retrieval.get("bank_loaded", False))
    memory_retrieval["entries"] = max(0, int(memory_retrieval.get("entries", 0) or 0))
    memory_retrieval["started_at"] = str(memory_retrieval.get("started_at", "") or "")
    memory_retrieval["completed_at"] = str(memory_retrieval.get("completed_at", "") or "")
    payload["memory_retrieval"] = memory_retrieval
    memory_precision = dict(cast(dict[str, Any], base["memory_precision"]))
    memory_precision.update(_mapping_to_dict(raw.get("memory_precision", {})))
    memory_precision["accepted"] = bool(memory_precision.get("accepted", False))
    memory_precision["quality"] = str(memory_precision.get("quality", "idle") or "idle")
    memory_precision["primary_similarity"] = _clip01(memory_precision.get("primary_similarity", 0.0))
    memory_precision["primary_precision"] = _clip01(memory_precision.get("primary_precision", 0.0))
    memory_precision["counter_similarity"] = _clip01(memory_precision.get("counter_similarity", 0.0))
    memory_precision["counter_precision"] = _clip01(memory_precision.get("counter_precision", 0.0))
    memory_precision["edge"] = round(_float_or(memory_precision.get("edge", 0.0), 0.0), 4)
    memory_precision["precision_edge"] = round(_float_or(memory_precision.get("precision_edge", 0.0), 0.0), 4)
    memory_precision["minimum_similarity"] = _clip01(
        memory_precision.get("minimum_similarity", _MEMORY_PRECISION_MIN_SIMILARITY)
    )
    memory_precision["minimum_precision"] = _clip01(
        memory_precision.get("minimum_precision", _MEMORY_PRECISION_MIN_SCORE)
    )
    memory_precision["minimum_edge"] = _clip01(memory_precision.get("minimum_edge", _MEMORY_PRECISION_MIN_EDGE))
    memory_precision["reason"] = str(memory_precision.get("reason", "") or "")
    payload["memory_precision"] = memory_precision
    payload["memory_direction"] = _upper_action(raw.get("memory_direction", "HOLD"))
    payload["model_council"] = _mapping_to_dict(raw.get("model_council", {}))
    payload["prediction_stack"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], raw.get("prediction_stack", []))
        if isinstance(item, Mapping)
    ][:3]

    primary_fit = dict(cast(dict[str, Any], base["primary_fit"]))
    primary_fit.update(_mapping_to_dict(raw.get("primary_fit", {})))
    primary_fit["summary"] = str(primary_fit.get("summary", base["summary"]) or base["summary"])
    primary_fit["transition_bias"] = _mapping_to_dict(primary_fit.get("transition_bias", {}))
    primary_fit["top_matches"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], primary_fit.get("top_matches", []))
        if isinstance(item, Mapping)
    ]
    primary_fit["top_predictions"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], primary_fit.get("top_predictions", []))
        if isinstance(item, Mapping)
    ]
    primary_fit["candle_templates"] = [str(item) for item in cast(Sequence[Any], primary_fit.get("candle_templates", []))]
    payload["primary_fit"] = primary_fit

    counter_fit = dict(cast(dict[str, Any], base["counter_fit"]))
    counter_fit.update(_mapping_to_dict(raw.get("counter_fit", {})))
    counter_fit["summary"] = str(counter_fit.get("summary", base["summary"]) or base["summary"])
    counter_fit["transition_bias"] = _mapping_to_dict(counter_fit.get("transition_bias", {}))
    counter_fit["top_matches"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], counter_fit.get("top_matches", []))
        if isinstance(item, Mapping)
    ]
    counter_fit["top_predictions"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], counter_fit.get("top_predictions", []))
        if isinstance(item, Mapping)
    ]
    counter_fit["candle_templates"] = [str(item) for item in cast(Sequence[Any], counter_fit.get("candle_templates", []))]
    payload["counter_fit"] = counter_fit

    forward_projection = dict(cast(dict[str, Any], base["forward_projection"]))
    forward_projection.update(_mapping_to_dict(raw.get("forward_projection", {})))
    forward_projection["headline"] = str(forward_projection.get("headline", base["summary"]) or base["summary"])
    forward_projection["path"] = str(forward_projection.get("path", base["summary"]) or base["summary"])
    forward_projection["trigger_area"] = str(forward_projection.get("trigger_area", "") or "")
    forward_projection["target_area"] = str(forward_projection.get("target_area", "") or "")
    forward_projection["invalidation_area"] = str(forward_projection.get("invalidation_area", "") or "")
    forward_projection["immediacy"] = str(forward_projection.get("immediacy", "forming") or "forming")
    forward_projection["projected_candles"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], forward_projection.get("projected_candles", []))
        if isinstance(item, Mapping)
    ]
    forward_projection["path_steps"] = [str(item) for item in cast(Sequence[Any], forward_projection.get("path_steps", []))]
    payload["forward_projection"] = forward_projection

    payload["hotspots"] = [
        dict(cast(Mapping[str, Any], item))
        for item in cast(Sequence[Any], raw.get("hotspots", []))
        if isinstance(item, Mapping)
    ]
    return payload


def _mark_memory_projection_payload_stale(
    value: Any,
    *,
    mode: str,
    frame_index: int,
    chart_path: str,
) -> dict[str, Any]:
    payload = _normalize_memory_projection_payload(value, mode=mode)
    current_chart_path = str(chart_path or "").strip()
    current_frame_index = int(frame_index or -1)
    source_frame_index = int(payload.get("source_frame_index", -1) or -1)
    source_chart_path = str(payload.get("source_chart_path", "") or "").strip()
    if current_chart_path and source_frame_index == current_frame_index and source_chart_path == current_chart_path:
        payload["is_current"] = True
        return payload
    payload["is_current"] = False
    if str(payload.get("status", "")).lower() == "ready":
        label = "Show Future" if str(payload.get("mode", "predict")) == "future" else "Predict"
        payload["status"] = "stale"
        payload["summary"] = (
            f"{str(payload.get('summary', _memory_projection_default_note(mode)) or _memory_projection_default_note(mode)).strip()} "
            f"New chart captured. Run {label} again for the latest frame."
        ).strip()
    return payload


def _default_tracking_summary(*, message: str = "") -> dict[str, Any]:
    return {
        "chart_valid": False,
        "surface_kind": "manual_focus_surface",
        "visible_candle_count": 0,
        "active_track_count": 0,
        "chart_region": {},
        "display_region": {},
        "detected_timeframe": "",
        "timeframe_source": "unconfirmed",
        "timeframe_confidence": 0.0,
        "detected_market": "",
        "market_source": "unconfirmed",
        "market_confidence": 0.0,
        "global_direction": "HOLD",
        "local_direction": "HOLD",
        "impulse_direction": "HOLD",
        "latest_candle_color": "unknown",
        "overlay_kind": "",
        "message": str(message or ""),
        "tracked_candles": [],
        "structure_boxes": [],
        "current_box": {},
        "projection": {},
        "candle_statistics": {},
        "behavior": {},
        "box_context": {},
        "trend_context": {},
        "entry_state": "WAIT",
        "entry_label": "WAIT",
        "entry_quality": "NONE",
        "phoenixguard_report": _default_phoenixguard_report(message=message or "Waiting for live chart structure.", status="warming"),
    }


class WindowCaptureBackend(Protocol):
    def list_windows(self, title_query: str | None = None) -> list[dict[str, Any]]:
        ...

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        ...


class BrokerExecutionBackend(Protocol):
    def is_supported(self) -> bool:
        ...

    def read_surface(self, image: Image.Image) -> dict[str, Any]:
        ...

    def prepare_and_click(
        self,
        *,
        descriptor: Mapping[str, Any],
        window_image: Image.Image,
        side: str,
        amount: str,
        expiry_seconds: int,
        broker_surface: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...


class FocusSelectionBackend(Protocol):
    def is_supported(self) -> bool:
        ...

    def arm_selection(
        self,
        *,
        session_id: str,
        descriptor: Mapping[str, Any],
        on_selected: Callable[[str, list[float], str], None],
        on_state_change: Callable[[str, dict[str, Any]], None],
    ) -> None:
        ...

    def cancel_selection(self, *, session_id: str | None = None) -> None:
        ...


@dataclass(slots=True)
class TrackingStudy:
    chart_image: Image.Image
    overlay_image: Image.Image
    chart_region: dict[str, Any]
    tracking_summary: dict[str, Any]
    latest_signal: dict[str, Any]


class WindowTrackingAdapter(Protocol):
    def study(self, image: Image.Image, *, session_payload: Mapping[str, Any] | None = None) -> TrackingStudy:
        ...

    def build_memory_projection(
        self,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        *,
        mode: str = "predict",
        session_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class WindowsNativeFocusSelectionBackend:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop_evt = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._overlay_thread: threading.Thread | None = None
        self._overlay_cancel_evt: threading.Event | None = None
        self._active_session_id = ""
        self._active_hwnd = 0
        self._active_title = ""
        self._on_selected: Callable[[str, list[float], str], None] | None = None
        self._on_state_change: Callable[[str, dict[str, Any]], None] | None = None
        self._last_combo_down = False

    def is_supported(self) -> bool:
        try:
            import os

            return os.name == "nt"
        except Exception:
            return False

    def arm_selection(
        self,
        *,
        session_id: str,
        descriptor: Mapping[str, Any],
        on_selected: Callable[[str, list[float], str], None],
        on_state_change: Callable[[str, dict[str, Any]], None],
    ) -> None:
        if not self.is_supported():
            raise ValueError("Native broker focus selection is available only on Windows.")

        hwnd = int(descriptor.get("hwnd", 0) or 0)
        if hwnd <= 0:
            raise ValueError("The broker window is missing a valid handle.")

        title = str(descriptor.get("title", "") or "").strip()
        with self._lock:
            if self._overlay_cancel_evt is not None:
                self._overlay_cancel_evt.set()
            self._overlay_cancel_evt = None
            self._active_session_id = str(session_id or "")
            self._active_hwnd = hwnd
            self._active_title = title
            self._on_selected = on_selected
            self._on_state_change = on_state_change
            self._last_combo_down = False
            self._ensure_monitor_thread_locked()

        on_state_change(
            str(session_id or ""),
            _focus_selector_state(
                supported=True,
                armed=True,
                active=False,
                status="armed",
                message=(
                    "Focus selector armed. Switch to Pocket Option, press Ctrl+V, "
                    "drag the chart region, then press Enter."
                ),
                target_hwnd=hwnd,
                target_title=title,
            ),
        )

    def cancel_selection(self, *, session_id: str | None = None) -> None:
        with self._lock:
            active_session_id = self._active_session_id
            if session_id and active_session_id and session_id != active_session_id:
                return
            if self._overlay_cancel_evt is not None:
                self._overlay_cancel_evt.set()
            self._overlay_cancel_evt = None
            self._active_session_id = ""
            self._active_hwnd = 0
            self._active_title = ""
            self._on_selected = None
            self._on_state_change = None
            self._last_combo_down = False

    def _ensure_monitor_thread_locked(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_hotkey_loop,
            name="window-tracker-focus-hotkey",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_hotkey_loop(self) -> None:
        if not self.is_supported():
            return
        import ctypes

        user32 = ctypes.windll.user32
        while not self._stop_evt.is_set():
            with self._lock:
                session_id = self._active_session_id
                hwnd = self._active_hwnd
                overlay_active = self._overlay_thread is not None and self._overlay_thread.is_alive()
            if not session_id or hwnd <= 0 or overlay_active:
                self._last_combo_down = False
                time.sleep(0.05)
                continue

            try:
                foreground_hwnd = int(user32.GetForegroundWindow() or 0)
                ctrl_down = bool(
                    (int(user32.GetAsyncKeyState(0x11)) & 0x8000)
                    or (int(user32.GetAsyncKeyState(0xA2)) & 0x8000)
                    or (int(user32.GetAsyncKeyState(0xA3)) & 0x8000)
                )
                v_down = bool(int(user32.GetAsyncKeyState(0x56)) & 0x8000)
            except Exception:
                time.sleep(0.08)
                continue

            combo_down = bool(ctrl_down and v_down)
            if combo_down and not self._last_combo_down and foreground_hwnd == hwnd:
                self._launch_overlay()
            self._last_combo_down = combo_down
            time.sleep(0.03)

    def _launch_overlay(self) -> None:
        with self._lock:
            if not self._active_session_id:
                return
            if self._overlay_thread is not None and self._overlay_thread.is_alive():
                return
            cancel_evt = threading.Event()
            self._overlay_cancel_evt = cancel_evt
            session_id = self._active_session_id
            hwnd = self._active_hwnd
            title = self._active_title
            callback = self._on_state_change
            self._overlay_thread = threading.Thread(
                target=self._run_overlay_selection,
                args=(session_id, hwnd, title, cancel_evt),
                name="window-tracker-focus-overlay",
                daemon=True,
            )
            self._overlay_thread.start()

        if callback is not None:
            callback(
                session_id,
                _focus_selector_state(
                    supported=True,
                    armed=True,
                    active=True,
                    status="selecting",
                    message=(
                        "Ctrl+V received. Drag directly on the broker surface, "
                        "then press Enter to save the tracker focus."
                    ),
                    target_hwnd=hwnd,
                    target_title=title,
                ),
            )

    def _run_overlay_selection(
        self,
        session_id: str,
        hwnd: int,
        title: str,
        cancel_evt: threading.Event,
    ) -> None:
        result: dict[str, Any] = {"status": "cancelled", "message": "Broker focus selection cancelled."}
        stderr_log = ""
        try:
            helper_path = Path(__file__).with_name("native_focus_overlay.py")
            if not helper_path.exists():
                raise RuntimeError(f"Native focus helper script is missing: {helper_path}")
            
            LOGGER.info(f"Starting native overlay for hwnd {hwnd} in session {session_id}")
            command = [str(sys.executable), str(helper_path), "--hwnd", str(int(hwnd))]
            
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                )
            except Exception as e:
                raise RuntimeError(f"Failed to start native overlay process: {e}")
            
            cancelled = False
            timed_out = False
            deadline = time.monotonic() + 190.0
            
            while process.poll() is None:
                if cancel_evt.is_set():
                    LOGGER.info(f"Native overlay cancelled for session {session_id}")
                    cancelled = True
                    process.terminate()
                    break
                if time.monotonic() >= deadline:
                    LOGGER.warning(f"Native overlay timed out after 190s for session {session_id}")
                    timed_out = True
                    process.terminate()
                    break
                time.sleep(0.04)
            
            try:
                stdout_text, stderr_text = process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                LOGGER.warning(f"Process communication timeout for session {session_id}, killing process")
                process.kill()
                try:
                    stdout_text, stderr_text = process.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    stdout_text, stderr_text = "", "Process killed due to timeout"

            stderr_log = str(stderr_text or "").strip()
            stdout_log = str(stdout_text or "").strip()

            if cancelled:
                result = {"status": "cancelled", "message": "Broker focus selection cancelled."}
                LOGGER.info(f"Native overlay cancelled for session {session_id}")
            elif timed_out:
                result = {
                    "status": "error",
                    "message": "Native broker focus selection timed out after 190 seconds.",
                }
                LOGGER.warning(f"Native overlay timeout for session {session_id}")
            else:
                parsed: dict[str, Any] = {}
                
                # Parse stdout for JSON response
                for line in reversed(stdout_log.splitlines()):
                    candidate = str(line or "").strip()
                    if not candidate:
                        continue
                    try:
                        row = json.loads(candidate)
                        if isinstance(row, Mapping):
                            parsed = _mapping_to_dict(row)
                            break
                    except json.JSONDecodeError:
                        continue
                
                if parsed:
                    result = parsed
                    LOGGER.info(f"Native overlay completed with status {parsed.get('status')} for session {session_id}")
                else:
                    # Detailed error reporting
                    if process.returncode is not None and process.returncode != 0:
                        error_msg = f"Native overlay exited with code {process.returncode}"
                        if stderr_log:
                            error_msg += f": {stderr_log[:200]}"
                        raise RuntimeError(error_msg)
                    elif stdout_log:
                        raise RuntimeError(f"Native overlay returned invalid response: {stdout_log[:200]}")
                    else:
                        raise RuntimeError("Native overlay returned no output or response")
        except Exception as exc:
            LOGGER.error(f"Native broker focus selection failed for session {session_id}: {exc}")
            if stderr_log:
                LOGGER.error(f"Overlay stderr: {stderr_log}")
            result = {
                "status": "error",
                "message": f"Native broker focus selection failed: {exc}",
            }

        self._complete_overlay_result(session_id, title, result)

    def _complete_overlay_result(
        self,
        session_id: str,
        title: str,
        result: Mapping[str, Any],
    ) -> None:
        on_selected: Callable[[str, list[float], str], None] | None = None
        on_state_change: Callable[[str, dict[str, Any]], None] | None = None
        hwnd = 0
        
        with self._lock:
            # Check if this session is still active
            if session_id != self._active_session_id:
                LOGGER.warning(
                    f"Overlay result received for inactive session {session_id} (active: {self._active_session_id})"
                )
                self._overlay_thread = None
                self._overlay_cancel_evt = None
                return
            
            on_selected = self._on_selected
            on_state_change = self._on_state_change
            hwnd = self._active_hwnd
            
            # Clear active session immediately to prevent race conditions
            self._active_session_id = ""
            self._active_hwnd = 0
            self._active_title = ""
            self._on_selected = None
            self._on_state_change = None
            self._overlay_cancel_evt = None
            self._overlay_thread = None
            self._last_combo_down = False

        # Process result outside lock to avoid deadlocks
        status = str(result.get("status", "cancelled") or "cancelled").strip().lower()
        
        if status == "selected":
            LOGGER.info(f"Broker focus selected for session {session_id}")
            try:
                normalized_bbox = _normalize_focus_region_bbox(cast(Sequence[Any], result.get("normalized_bbox", [])))
                if on_selected is not None:
                    on_selected(
                        session_id,
                        normalized_bbox,
                        str(result.get("source", "native_ctrl_v_window") or "native_ctrl_v_window"),
                    )
            except Exception as e:
                LOGGER.error(f"Failed to process selected focus region for session {session_id}: {e}")
            return

        if on_state_change is None:
            LOGGER.warning(f"No state change callback for session {session_id}")
            return

        if status == "error":
            message = str(result.get("message", "Native broker focus selection failed.") or "Native broker focus selection failed.")
            LOGGER.error(f"Broker focus selection error for session {session_id}: {message}")
            on_state_change(
                session_id,
                _focus_selector_state(
                    supported=True,
                    armed=False,
                    active=False,
                    status="error",
                    message=message,
                    last_error=message,
                    target_hwnd=hwnd,
                    target_title=title,
                ),
            )
            return

        LOGGER.info(f"Broker focus selection {status} for session {session_id}")
        on_state_change(
            session_id,
            _focus_selector_state(
                supported=True,
                armed=False,
                active=False,
                status="cancelled",
                message=str(result.get("message", "Broker focus selection cancelled.") or "Broker focus selection cancelled."),
                target_hwnd=hwnd,
                target_title=title,
            ),
        )


class WindowsWindowCaptureBackend:
    _dpi_awareness_attempted = False

    def is_windows(self) -> bool:
        return self._is_windows()

    def list_windows(self, title_query: str | None = None) -> list[dict[str, Any]]:
        if hasattr(self, "_list_windows_override"):
            return cast(list[dict[str, Any]], getattr(self, "_list_windows_override")(title_query))

        if not self._is_windows():
            return []
        self._ensure_dpi_awareness()
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        query = str(title_query or "").strip().lower()
        windows: list[dict[str, Any]] = []

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd: int, _lparam: int) -> bool:
            if not bool(user32.IsWindowVisible(hwnd)):
                return True
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = str(buffer.value or "").strip()
            if not title:
                return True
            rect = wintypes.RECT()
            if not bool(user32.GetWindowRect(hwnd, ctypes.byref(rect))):
                return True
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 64 or height < 64:
                return True
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "bbox": [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)],
                    "width": width,
                    "height": height,
                    "is_minimized": bool(user32.IsIconic(hwnd)),
                }
            )
            return True

        user32.EnumWindows(enum_proc(_callback), 0)
        windows.sort(key=lambda item: (len(str(item.get("title", ""))), int(item.get("hwnd", 0))), reverse=True)
        if not query:
            return windows
        return [item for item in windows if _title_matches_window_query(item.get("title", ""), query)]

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        if hasattr(self, "_capture_window_override"):
            return cast(Image.Image, getattr(self, "_capture_window_override")(descriptor))
        if self._is_windows():
            self._ensure_dpi_awareness()
        hwnd = int(descriptor.get("hwnd", 0) or 0)
        title = str(descriptor.get("title", "") or "")
        compact_title = _compact_text(title)
        pocket_option_window = _is_pocket_option_query(title)
        prefer_imagegrab = not pocket_option_window and any(
            _compact_text(token) and _compact_text(token) in compact_title for token in _BROWSER_TITLE_TOKENS
        )

        if pocket_option_window and hwnd > 0 and self._is_windows():
            offscreen = self._capture_window_printwindow(hwnd, descriptor)
            if (
                offscreen is not None
                and not self._looks_blank(offscreen)
                and not self._looks_browser_content_blank(offscreen)
            ):
                return offscreen.convert("RGB")
            try:
                live_capture = self._capture_window_imagegrab(descriptor)
                if not self._looks_blank(live_capture) and not self._looks_browser_content_blank(live_capture):
                    return live_capture.convert("RGB")
            except Exception:
                LOGGER.debug("Pocket Option live ImageGrab fallback failed.", exc_info=True)
            if offscreen is not None and not self._looks_blank(offscreen):
                return offscreen.convert("RGB")
        elif prefer_imagegrab:
            live_capture = self._capture_window_imagegrab(descriptor)
            if not self._looks_blank(live_capture):
                return live_capture.convert("RGB")
        if hwnd > 0 and self._is_windows():
            offscreen = self._capture_window_printwindow(hwnd, descriptor)
            if offscreen is not None and not self._looks_blank(offscreen):
                return offscreen.convert("RGB")
        return self._capture_window_imagegrab(descriptor)

    def _capture_window_imagegrab(self, descriptor: Mapping[str, Any]) -> Image.Image:
        bbox = cast(Sequence[Any], descriptor.get("bbox", []))
        if len(bbox) < 4:
            raise RuntimeError("Window descriptor is missing a valid bounding box.")
        rect = (
            int(float(bbox[0])),
            int(float(bbox[1])),
            int(float(bbox[2])),
            int(float(bbox[3])),
        )
        try:
            return ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
        except TypeError:
            return ImageGrab.grab(bbox=rect).convert("RGB")

    def _capture_window_printwindow(
        self,
        hwnd: int,
        descriptor: Mapping[str, Any],
    ) -> Image.Image | None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        pw_render_full_content = 0x00000002
        src_copy = 0x00CC0020
        bi_rgb = 0
        dib_rgb_colors = 0

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3),
            ]

        width, height = self._window_capture_dimensions(hwnd, descriptor)
        if width < 32 or height < 32:
            return None

        hwnd_dc = user32.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None
        mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
        if not mem_dc or not bitmap:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(hwnd, hwnd_dc)
            return None

        old_obj = gdi32.SelectObject(mem_dc, bitmap)
        image: Image.Image | None = None
        try:
            success = bool(user32.PrintWindow(hwnd, mem_dc, pw_render_full_content))
            if not success:
                success = bool(user32.PrintWindow(hwnd, mem_dc, 0))
            if not success and not bool(user32.IsIconic(hwnd)):
                success = bool(gdi32.BitBlt(mem_dc, 0, 0, width, height, hwnd_dc, 0, 0, src_copy))
            if not success:
                return None

            bitmap_info = BITMAPINFO()
            bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bitmap_info.bmiHeader.biWidth = width
            bitmap_info.bmiHeader.biHeight = -height
            bitmap_info.bmiHeader.biPlanes = 1
            bitmap_info.bmiHeader.biBitCount = 32
            bitmap_info.bmiHeader.biCompression = bi_rgb
            buffer = ctypes.create_string_buffer(width * height * 4)
            got_bits = gdi32.GetDIBits(
                mem_dc,
                bitmap,
                0,
                height,
                buffer,
                ctypes.byref(bitmap_info),
                dib_rgb_colors,
            )
            if not got_bits:
                return None
            image = Image.frombuffer(
                "RGB",
                (width, height),
                buffer.raw,
                "raw",
                "BGRX",
                0,
                1,
            ).copy()
        finally:
            if old_obj:
                gdi32.SelectObject(mem_dc, old_obj)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(hwnd, hwnd_dc)
        return image

    def _window_capture_dimensions(self, hwnd: int, descriptor: Mapping[str, Any]) -> tuple[int, int]:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        width = 0
        height = 0
        # Focus selection is normalized against GetWindowRect in native_focus_overlay.py.
        # Keep PrintWindow captures in that same coordinate space so the locked region
        # cannot be mapped onto a smaller client-area image and lose selected pixels.
        if bool(user32.GetWindowRect(hwnd, ctypes.byref(rect))):
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
        if width < 32 or height < 32:
            width = int(descriptor.get("width", 0) or 0)
            height = int(descriptor.get("height", 0) or 0)
        if width < 32 or height < 32:
            if bool(user32.GetClientRect(hwnd, ctypes.byref(rect))):
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
        return max(0, width), max(0, height)

    def _looks_blank(self, image: Image.Image) -> bool:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if arr.size == 0:
            return True
        return bool(float(arr.std()) <= 1.0)

    def _looks_browser_content_blank(self, image: Image.Image) -> bool:
        width, height = image.size
        if width < 600 or height < 360:
            return False
        # Edge/Chrome PrintWindow can return valid browser chrome while the web
        # canvas is a flat dark rectangle. Treat that as unusable for broker CV
        # and fall back to a live screen grab when the window is visible.
        x0 = int(round(width * 0.16))
        y0 = int(round(height * 0.14))
        x1 = max(x0 + 32, width - 20)
        y1 = max(y0 + 32, height - 20)
        content = np.asarray(image.convert("RGB").crop((x0, y0, x1, y1)), dtype=np.uint8)
        if content.size == 0:
            return True
        channel_span = int(content.max()) - int(content.min())
        return bool(float(content.std()) <= 3.0 and channel_span <= 18)

    def _is_windows(self) -> bool:
        try:
            import os

            return os.name == "nt"
        except Exception:
            return False

    def _ensure_dpi_awareness(self) -> None:
        if self.__class__._dpi_awareness_attempted:
            return
        self.__class__._dpi_awareness_attempted = True
        if not self._is_windows():
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
            if set_context is not None:
                set_context.argtypes = [wintypes.HANDLE]
                set_context.restype = wintypes.BOOL
                per_monitor_v2 = ctypes.c_void_p(-4)
                if bool(set_context(per_monitor_v2)):
                    return
            try:
                shcore = ctypes.windll.shcore
                set_awareness = getattr(shcore, "SetProcessDpiAwareness", None)
                if set_awareness is not None:
                    set_awareness.argtypes = [ctypes.c_int]
                    set_awareness.restype = ctypes.c_long
                    if int(set_awareness(2)) in {0, -2147024891}:
                        return
            except Exception:
                pass
            set_aware = getattr(user32, "SetProcessDPIAware", None)
            if set_aware is not None:
                set_aware()
        except Exception:
            LOGGER.debug("Unable to set process DPI awareness for window capture.", exc_info=True)


class PocketOptionBrokerExecutionBackend:
    def __init__(self, *, identity_adapter: Any | None = None) -> None:
        self._identity_adapter = identity_adapter

    def is_supported(self) -> bool:
        try:
            import os

            return os.name == "nt"
        except Exception:
            return False

    def _identity_reader(self) -> Any:
        if self._identity_adapter is None:
            self._identity_adapter = PhoenixGuardWindowTrackingAdapter()
        return self._identity_adapter

    def _read_identity(self, image: Image.Image) -> dict[str, Any]:
        try:
            reader = self._identity_reader()
            timeframe = _mapping_to_dict(reader._detect_timeframe_selector(image))  # noqa: SLF001
            market = _mapping_to_dict(reader._detect_market_selector(image, timeframe_selector=timeframe))  # noqa: SLF001
        except Exception:
            LOGGER.debug("Broker identity read failed.", exc_info=True)
            timeframe = {}
            market = {}

        detected_market = _normalize_fx_market_candidate(market.get("value", ""))
        detected_timeframe = str(timeframe.get("value", "") or "").strip().upper()
        market_confidence = _clip01(market.get("confidence", 0.0)) if detected_market else 0.0
        timeframe_confidence = _clip01(timeframe.get("confidence", 0.0))
        identity_ready = bool(detected_market and detected_timeframe and market_confidence >= 0.42 and timeframe_confidence >= 0.42)
        return {
            "detected_market": detected_market,
            "market_source": f"broker_{str(market.get('source', 'unconfirmed') or 'unconfirmed')}",
            "market_confidence": market_confidence,
            "market_bbox": list(cast(Sequence[Any], market.get("bbox", []))) if market else [],
            "market_raw_text": str(market.get("raw_text", "") or ""),
            "detected_timeframe": detected_timeframe,
            "timeframe_source": f"broker_{str(timeframe.get('source', 'unconfirmed') or 'unconfirmed')}",
            "timeframe_confidence": timeframe_confidence,
            "timeframe_bbox": list(cast(Sequence[Any], timeframe.get("bbox", []))) if timeframe else [],
            "identity_ready": identity_ready,
            "identity_message": (
                f"Broker identity read as {detected_market} {detected_timeframe}."
                if identity_ready
                else "Broker market/timeframe identity is not confident enough for live execution."
            ),
        }

    @staticmethod
    def _bbox_visible_in_image(
        bbox: Any,
        *,
        image_width: int,
        image_height: int,
        edge_margin: int = 2,
        allow_top_edge: bool = False,
    ) -> bool:
        row = cast(Sequence[Any], bbox if isinstance(bbox, (list, tuple)) else [])
        if len(row) < 4:
            return False
        try:
            x0, y0, x1, y1 = [int(round(float(value))) for value in row[:4]]
        except (TypeError, ValueError):
            return False
        return bool(
            x1 > x0
            and y1 > y0
            and x0 >= edge_margin
            and (y0 >= edge_margin or (allow_top_edge and y0 >= 0))
            and x1 <= max(edge_margin, int(image_width) - edge_margin)
            and y1 <= max(edge_margin, int(image_height) - edge_margin)
        )

    @classmethod
    def _control_visibility_payload(
        cls,
        *,
        image_width: int,
        image_height: int,
        buy: Mapping[str, Any] | None,
        sell: Mapping[str, Any] | None,
        amount_field: Mapping[str, Any] | None,
        time_field: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        rows = {
            "buy_visible": cls._bbox_visible_in_image(
                (buy or {}).get("bbox", []),
                image_width=image_width,
                image_height=image_height,
            ),
            "sell_visible": cls._bbox_visible_in_image(
                (sell or {}).get("bbox", []),
                image_width=image_width,
                image_height=image_height,
            ),
            "amount_visible": cls._bbox_visible_in_image(
                (amount_field or {}).get("bbox", []),
                image_width=image_width,
                image_height=image_height,
            ),
            "time_visible": cls._bbox_visible_in_image(
                (time_field or {}).get("bbox", []),
                image_width=image_width,
                image_height=image_height,
                allow_top_edge=True,
            ),
        }
        all_required = all(bool(value) for value in rows.values())
        return {
            "image_width": int(image_width),
            "image_height": int(image_height),
            **rows,
            "all_required_visible": all_required,
            "message": (
                "Full broker window contains BUY, SELL, amount, and expiry controls."
                if all_required
                else "Full broker window is missing or clipping one of BUY, SELL, amount, or expiry controls."
            ),
        }

    def read_surface(self, image: Image.Image) -> dict[str, Any]:
        surface = image.convert("RGB")
        arr = np.asarray(surface, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[0] < 120 or arr.shape[1] < 180:
            return _default_broker_surface_payload(message="Broker surface is too small for control detection.")
        buy = self._find_button(arr, "BUY")
        sell = self._find_button(arr, "SELL")
        amount_field = self._derive_amount_field(arr.shape[1], arr.shape[0], buy, sell)
        time_field = self._derive_time_field(arr.shape[1], arr.shape[0], amount_field)
        control_visibility = self._control_visibility_payload(
            image_width=int(arr.shape[1]),
            image_height=int(arr.shape[0]),
            buy=buy,
            sell=sell,
            amount_field=amount_field,
            time_field=time_field,
        )
        controls_ready = bool(
            buy
            and sell
            and amount_field
            and time_field
            and bool(control_visibility.get("all_required_visible", False))
        )
        expiry_read = self._read_expiry_time_text(surface, time_field) if time_field else {}
        confidence_values = [
            _clip01(buy.get("confidence", 0.0)) if buy else 0.0,
            _clip01(sell.get("confidence", 0.0)) if sell else 0.0,
            _clip01(amount_field.get("confidence", 0.0)) if amount_field else 0.0,
        ]
        confidence = float(np.mean(np.asarray(confidence_values, dtype=np.float32))) if confidence_values else 0.0
        identity = self._read_identity(surface)
        read_at = _now_iso()
        payload = _default_broker_surface_payload(
            message=(
                "Pocket Option controls detected."
                if controls_ready
                else "Pocket Option BUY/SELL controls are not confidently detected."
            )
        )
        payload.update(
            {
                "state": "ready" if controls_ready else "blocked",
                "read_at": read_at,
                "controls_ready": controls_ready,
                "control_visibility": control_visibility,
                "order_panel": self._derive_order_panel(arr.shape[1], arr.shape[0], buy, sell, amount_field, time_field),
                "buy_button": buy,
                "sell_button": sell,
                "amount_field": amount_field,
                "time_field": time_field,
                "execution_boxes": self._execution_boxes_payload(
                    image_width=int(arr.shape[1]),
                    image_height=int(arr.shape[0]),
                    read_at=read_at,
                    buy=buy,
                    sell=sell,
                    amount_field=amount_field,
                    time_field=time_field,
                ),
                "expiry_lock": {
                    "required_seconds": _EXECUTION_DEFAULT_EXPIRY_SEC,
                    "configured_seconds": 0,
                    "configured_text": "",
                    "field_ready": bool(time_field),
                    "confidence": _clip01(time_field.get("confidence", 0.0)) if time_field else 0.0,
                    "visible_text": str(expiry_read.get("text", "") or ""),
                    "visible_seconds": int(expiry_read.get("seconds", 0) or 0),
                    "visible_confidence": _clip01(expiry_read.get("confidence", 0.0)),
                    "message": (
                        "Expiry/time field found; live execution will set the calculated duration before clicking."
                        if time_field
                        else "Expiry/time field not found. Live execution is blocked."
                    ),
                },
                **identity,
                "amount_lock": {
                    "required": _FIXED_BROKER_AMOUNT,
                    "configured": _FIXED_BROKER_AMOUNT,
                    "verified": bool(amount_field),
                    "confidence": _clip01(amount_field.get("confidence", 0.0)) if amount_field else 0.0,
                    "message": (
                        "Amount field found; live execution will set $5 immediately before clicking."
                        if amount_field
                        else "Amount field not found. Live execution is blocked."
                    ),
                },
                "confidence": _clip01(confidence),
            }
        )
        return payload

    def prepare_and_click(
        self,
        *,
        descriptor: Mapping[str, Any],
        window_image: Image.Image,
        side: str,
        amount: str,
        expiry_seconds: int,
        broker_surface: Mapping[str, Any],
    ) -> dict[str, Any]:
        side_action = _upper_action(side)
        target_expiry_seconds = self._coerce_expiry_seconds(expiry_seconds)
        if side_action not in {"BUY", "SELL"}:
            return {"status": "blocked", "message": "Execution side is not BUY or SELL."}
        if str(amount or "").strip() != _FIXED_BROKER_AMOUNT:
            return {"status": "blocked", "message": "Amount lock rejected execution because amount is not fixed at $5."}
        if not self.is_supported():
            return {"status": "blocked", "message": "Live broker clicking is supported only on Windows."}
        surface = _mapping_to_dict(broker_surface)
        amount_field = _mapping_to_dict(surface.get("amount_field", {}))
        time_field = _mapping_to_dict(surface.get("time_field", {}))
        expiry_lock = _mapping_to_dict(surface.get("expiry_lock", {}))
        execution_boxes = _mapping_to_dict(surface.get("execution_boxes", {}))
        side_button = _mapping_to_dict(surface.get("buy_button" if side_action == "BUY" else "sell_button", {}))
        if not bool(surface.get("controls_ready", False)) or not amount_field or not side_button:
            return {"status": "blocked", "message": "Broker controls are not ready for live clicking."}
        if not time_field:
            return {"status": "blocked", "message": "Expiry/time field is not ready for live clicking."}

        try:
            hwnd = int(descriptor.get("hwnd", 0) or 0)
            window_bbox = cast(Sequence[Any], descriptor.get("bbox", []))
            if len(window_bbox) < 4:
                return {"status": "blocked", "message": "Window descriptor is missing a screen bounding box."}
            window_left = int(round(float(window_bbox[0])))
            window_top = int(round(float(window_bbox[1])))
            amount_point = self._bbox_center(amount_field.get("bbox", []))
            time_point = self._bbox_center(time_field.get("bbox", []))
            button_point = self._bbox_center(side_button.get("bbox", []))
            if amount_point is None or time_point is None or button_point is None:
                return {"status": "blocked", "message": "Broker click targets are missing centers."}
            expiry_text = self._format_expiry_text(target_expiry_seconds)

            import ctypes

            user32 = ctypes.windll.user32
            self._ensure_cursor_dpi_awareness()
            activation = self._activate_locked_window_for_click(user32, hwnd)
            if bool(activation.get("is_minimized", False)):
                return {
                    "status": "blocked",
                    "message": "Locked Pocket Option window is minimized; live execution blocked instead of changing the browser window.",
                    "side": side_action,
                    "amount": _FIXED_BROKER_AMOUNT,
                    "expiry_seconds": int(target_expiry_seconds),
                    "expiry_text": expiry_text,
                    "time_field": time_field,
                    "window_size": [int(window_image.width), int(window_image.height)],
                }
            activation_bbox = cast(Sequence[Any], activation.get("bbox", []))
            if len(activation_bbox) >= 4:
                window_left = int(round(float(activation_bbox[0])))
                window_top = int(round(float(activation_bbox[1])))
            time_screen_point = (window_left + time_point[0], window_top + time_point[1])
            amount_screen_point = (window_left + amount_point[0], window_top + amount_point[1])
            button_screen_point = (window_left + button_point[0], window_top + button_point[1])
            time_cursor_point = self._cursor_point_from_physical_screen_point(user32, hwnd, *time_screen_point)
            amount_cursor_point = self._cursor_point_from_physical_screen_point(user32, hwnd, *amount_screen_point)
            button_cursor_point = self._cursor_point_from_physical_screen_point(user32, hwnd, *button_screen_point)
            expiry_result = self._set_expiry_with_popup(
                user32,
                hwnd=hwnd,
                descriptor=descriptor,
                window_left=window_left,
                window_top=window_top,
                time_field=time_field,
                expiry_seconds=target_expiry_seconds,
                current_seconds=int(expiry_lock.get("visible_seconds", 0) or 0) or None,
            )
            expiry_clicks = cast(list[dict[str, Any]], expiry_result.get("clicks", []))
            expiry_verification = _mapping_to_dict(expiry_result.get("verification", {}))
            expiry_geometry = _mapping_to_dict(expiry_result.get("geometry", {}))
            expiry_popup_locks = _mapping_to_dict(expiry_result.get("popup_locks", {}))
            if expiry_popup_locks:
                execution_boxes["popup_controls"] = expiry_popup_locks
            if str(expiry_result.get("status", "") or "").lower() == "blocked":
                return {
                    "status": "blocked",
                    "message": str(expiry_result.get("message", "") or "Expiry popup controls were not visually locked."),
                    "side": side_action,
                    "amount": _FIXED_BROKER_AMOUNT,
                    "expiry_seconds": int(target_expiry_seconds),
                    "expiry_text": expiry_text,
                    "expiry_popup_clicks": expiry_clicks,
                    "expiry_popup_geometry": expiry_geometry,
                    "expiry_verification": expiry_verification,
                    "expiry_popup_locks": expiry_popup_locks,
                    "visible_expiry_before": str(expiry_lock.get("visible_text", "") or ""),
                    "time_field": time_field,
                    "execution_boxes": execution_boxes,
                    "window_size": [int(window_image.width), int(window_image.height)],
                }
            verification_status = str(expiry_verification.get("status", "") or "").lower()
            if not bool(expiry_verification.get("matches", False)):
                return {
                    "status": "blocked",
                    "message": (
                        "Expiry verification blocked the broker click because the visible timer "
                        f"did not match {expiry_text}."
                        if verification_status != "unavailable"
                        else f"Expiry verification was unavailable after attempting to set {expiry_text}."
                    ),
                    "side": side_action,
                    "amount": _FIXED_BROKER_AMOUNT,
                    "expiry_seconds": int(target_expiry_seconds),
                    "expiry_text": expiry_text,
                    "expiry_popup_clicks": expiry_clicks,
                    "expiry_popup_geometry": expiry_geometry,
                    "expiry_verification": expiry_verification,
                    "expiry_popup_locks": expiry_popup_locks,
                    "visible_expiry_before": str(expiry_lock.get("visible_text", "") or ""),
                    "time_field": time_field,
                    "execution_boxes": execution_boxes,
                    "window_size": [int(window_image.width), int(window_image.height)],
                }
            refreshed_window_image: Image.Image | None = None
            try:
                refreshed_window_image = WindowsWindowCaptureBackend().capture_window(descriptor).convert("RGB")
                refreshed_surface = self.read_surface(refreshed_window_image)
            except Exception:
                LOGGER.debug("Broker surface refresh after expiry adjustment was unavailable.", exc_info=True)
                refreshed_surface = {}
            if refreshed_surface:
                refreshed_amount_field = _mapping_to_dict(refreshed_surface.get("amount_field", {}))
                refreshed_time_field = _mapping_to_dict(refreshed_surface.get("time_field", {}))
                refreshed_side_button = _mapping_to_dict(
                    refreshed_surface.get("buy_button" if side_action == "BUY" else "sell_button", {})
                )
                if not bool(refreshed_surface.get("controls_ready", False)) or not refreshed_amount_field or not refreshed_side_button:
                    return {
                        "status": "blocked",
                        "message": "Broker controls were not stable after expiry adjustment; trade button click was blocked.",
                        "side": side_action,
                        "amount": _FIXED_BROKER_AMOUNT,
                        "expiry_seconds": int(target_expiry_seconds),
                        "expiry_text": expiry_text,
                        "expiry_popup_clicks": expiry_clicks,
                        "expiry_popup_geometry": expiry_geometry,
                        "expiry_verification": expiry_verification,
                        "expiry_popup_locks": expiry_popup_locks,
                        "post_expiry_surface": {
                            "controls_ready": bool(refreshed_surface.get("controls_ready", False)),
                            "message": str(refreshed_surface.get("message", "") or ""),
                        },
                        "execution_boxes": execution_boxes,
                        "window_size": [
                            int((refreshed_window_image or window_image).width),
                            int((refreshed_window_image or window_image).height),
                        ],
                    }
                amount_field = refreshed_amount_field
                time_field = refreshed_time_field or time_field
                side_button = refreshed_side_button
                execution_boxes = _mapping_to_dict(refreshed_surface.get("execution_boxes", execution_boxes))
                if expiry_popup_locks:
                    execution_boxes["popup_controls"] = expiry_popup_locks
                amount_point = self._bbox_center(amount_field.get("bbox", []))
                time_point = self._bbox_center(time_field.get("bbox", []))
                button_point = self._bbox_center(side_button.get("bbox", []))
                if amount_point is None or time_point is None or button_point is None:
                    return {"status": "blocked", "message": "Broker click targets disappeared after expiry adjustment."}
                time_screen_point = (window_left + time_point[0], window_top + time_point[1])
                amount_screen_point = (window_left + amount_point[0], window_top + amount_point[1])
                button_screen_point = (window_left + button_point[0], window_top + button_point[1])
                time_cursor_point = self._cursor_point_from_physical_screen_point(user32, hwnd, *time_screen_point)
                amount_cursor_point = self._cursor_point_from_physical_screen_point(user32, hwnd, *amount_screen_point)
                button_cursor_point = self._cursor_point_from_physical_screen_point(user32, hwnd, *button_screen_point)
            # WAIT FOR EXPIRY POPUP TO FULLY CLOSE AND SETTLE
            time.sleep(0.35)
            self._activate_locked_window_for_click(user32, hwnd)
            time.sleep(0.12)
            
            # CLICK ON AMOUNT FIELD TO SET FIXED AMOUNT
            amount_click = self._click_screen_point(
                user32,
                amount_cursor_point[0],
                amount_cursor_point[1],
                expected_hwnd=hwnd,
                target_name="amount_field",
                target_bbox=self._screen_bbox_from_local(window_left, window_top, amount_field.get("bbox", [])),
                physical_point=amount_screen_point,
            )
            time.sleep(0.12)
            
            # CLEAR AND SET AMOUNT
            self._send_ctrl_a(user32)
            time.sleep(0.08)
            self._send_text(user32, _FIXED_BROKER_AMOUNT)
            time.sleep(0.10)
            
            # CLOSE AMOUNT EDITOR AND DISMISS ANY POPUPS
            self._send_enter(user32)
            time.sleep(0.10)
            self._send_escape(user32)
            time.sleep(0.28)
            
            # RE-ACTIVATE WINDOW AND WAIT FOR STABLE STATE
            self._activate_locked_window_for_click(user32, hwnd)
            time.sleep(0.16)
            
            # FINAL PRE-CLICK VERIFICATION - ENSURE BUTTON STILL VISIBLE
            try:
                final_surface = self.read_surface(WindowsWindowCaptureBackend().capture_window(descriptor).convert("RGB"))
                final_button = _mapping_to_dict(final_surface.get("buy_button" if side_action == "BUY" else "sell_button", {}))
                final_button_point = self._bbox_center(final_button.get("bbox", []))
                if final_button_point is not None:
                    button_screen_point = (window_left + final_button_point[0], window_top + final_button_point[1])
                    button_cursor_point = self._cursor_point_from_physical_screen_point(user32, hwnd, *button_screen_point)
            except Exception:
                LOGGER.debug("Pre-click button verification failed; using previous position.", exc_info=True)
            
            # LOCK WINDOW FOR BUTTON CLICK
            self._activate_locked_window_for_click(user32, hwnd)
            time.sleep(0.14)
            
            # CLICK THE BUY/SELL BUTTON
            button_click = self._click_screen_point(
                user32,
                button_cursor_point[0],
                button_cursor_point[1],
                expected_hwnd=hwnd,
                target_name=f"{side_action.lower()}_button",
                target_bbox=self._screen_bbox_from_local(window_left, window_top, side_button.get("bbox", [])),
                physical_point=button_screen_point,
            )
            
            # WAIT FOR BROKER TO PROCESS THE CLICK
            time.sleep(0.45)
            
            # VERIFY TRADE ACCEPTANCE
            trade_verification = self._verify_trade_click_result(
                descriptor=descriptor,
                side=side_action,
                button_bbox=side_button.get("bbox", []),
                expiry_seconds=target_expiry_seconds,
            )
            
            # RETRY LOGIC - IF VERIFICATION UNAVAILABLE, WAIT AND TRY ONCE MORE
            if str(trade_verification.get("status", "")).lower() == "unavailable" and bool(trade_verification.get("sent_input", True)):
                time.sleep(0.50)
                trade_verification = self._verify_trade_click_result(
                    descriptor=descriptor,
                    side=side_action,
                    button_bbox=side_button.get("bbox", []),
                    expiry_seconds=target_expiry_seconds,
                )
            
            confirmed = bool(trade_verification.get("confirmed", False))
            result_status = "clicked" if confirmed else "click_sent_unverified"
            result_message = (
                f"Clicked {side_action} with fixed ${_FIXED_BROKER_AMOUNT} and visually confirmed broker acceptance."
                if confirmed
                else f"Sent {side_action} click with fixed ${_FIXED_BROKER_AMOUNT}, but broker-side acceptance was not visually confirmed."
            )
            return {
                "status": result_status,
                "message": result_message,
                "side": side_action,
                "amount": _FIXED_BROKER_AMOUNT,
                "expiry_seconds": int(target_expiry_seconds),
                "expiry_text": expiry_text,
                "time_point": [int(time_cursor_point[0]), int(time_cursor_point[1])],
                "amount_point": [int(amount_cursor_point[0]), int(amount_cursor_point[1])],
                "button_point": [int(button_cursor_point[0]), int(button_cursor_point[1])],
                "physical_time_point": [int(time_screen_point[0]), int(time_screen_point[1])],
                "physical_amount_point": [int(amount_screen_point[0]), int(amount_screen_point[1])],
                "physical_button_point": [int(button_screen_point[0]), int(button_screen_point[1])],
                "expiry_popup_clicks": expiry_clicks,
                "expiry_popup_geometry": expiry_geometry,
                "expiry_verification": expiry_verification,
                "expiry_popup_locks": expiry_popup_locks,
                "visible_expiry_before": str(expiry_lock.get("visible_text", "") or ""),
                "click_diagnostics": [amount_click, button_click],
                "trade_verification": trade_verification,
                "execution_boxes": execution_boxes,
                "amount_commit": {
                    "amount": _FIXED_BROKER_AMOUNT,
                    "sent_ctrl_a": True,
                    "sent_enter": True,
                    "sent_escape": True,
                    "message": "Fixed amount was committed and editor popups were dismissed before the trade button click.",
                },
                "time_field": time_field,
                "window_size": [int(window_image.width), int(window_image.height)],
            }
        except Exception as exc:
            LOGGER.exception("Pocket Option live click failed.")
            return {"status": "error", "message": f"Pocket Option live click failed: {exc}"}

    @staticmethod
    def _ensure_cursor_dpi_awareness() -> None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
            if set_context is not None:
                try:
                    set_context.argtypes = [wintypes.HANDLE]
                    set_context.restype = wintypes.BOOL
                    set_context(ctypes.c_void_p(-4))
                except Exception:
                    pass
            try:
                shcore = ctypes.windll.shcore
                set_awareness = getattr(shcore, "SetProcessDpiAwareness", None)
                if set_awareness is not None:
                    set_awareness(2)
            except Exception:
                pass
            set_process_aware = getattr(user32, "SetProcessDPIAware", None)
            if set_process_aware is not None:
                try:
                    set_process_aware()
                except Exception:
                    pass
        except Exception:
            LOGGER.debug("Unable to force cursor DPI awareness.", exc_info=True)

    @staticmethod
    def _bbox_center(bbox: Any) -> tuple[int, int] | None:
        row = cast(Sequence[Any], bbox if isinstance(bbox, (list, tuple)) else [])
        if len(row) < 4:
            return None
        x0, y0, x1, y1 = [float(value) for value in row[:4]]
        return int(round((x0 + x1) * 0.5)), int(round((y0 + y1) * 0.5))

    @staticmethod
    def _normalized_bbox(bbox: Any) -> list[int]:
        row = cast(Sequence[Any], bbox if isinstance(bbox, (list, tuple)) else [])
        if len(row) < 4:
            return []
        try:
            x0, y0, x1, y1 = [int(round(float(value))) for value in row[:4]]
        except (TypeError, ValueError):
            return []
        if x1 <= x0 or y1 <= y0:
            return []
        return [x0, y0, x1, y1]

    @classmethod
    def _control_lock(
        cls,
        *,
        key: str,
        label: str,
        row: Mapping[str, Any],
        read_at: str,
        image_width: int,
        image_height: int,
        role: str = "control",
    ) -> dict[str, Any]:
        bbox = cls._normalized_bbox(row.get("bbox", []))
        center = cls._bbox_center(bbox) if bbox else None
        visible = cls._bbox_visible_in_image(
            bbox,
            image_width=max(1, int(image_width)),
            image_height=max(1, int(image_height)),
            allow_top_edge=True,
        ) if bbox else False
        confidence = _clip01(row.get("confidence", 0.0))
        locked = bool(bbox and center is not None and visible and confidence > 0.0)
        return {
            "key": str(key or ""),
            "label": str(label or key or ""),
            "role": str(role or "control"),
            "bbox": bbox,
            "center": [int(center[0]), int(center[1])] if center is not None else [],
            "confidence": confidence,
            "source": str(row.get("source", "unconfirmed") or "unconfirmed"),
            "visible": visible,
            "locked": locked,
            "read_at": str(read_at or ""),
        }

    @classmethod
    def _execution_boxes_payload(
        cls,
        *,
        image_width: int,
        image_height: int,
        read_at: str,
        buy: Mapping[str, Any],
        sell: Mapping[str, Any],
        amount_field: Mapping[str, Any],
        time_field: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "time_field": cls._control_lock(
                key="time_field",
                label="Time",
                row=time_field,
                read_at=read_at,
                image_width=image_width,
                image_height=image_height,
                role="time",
            ),
            "amount_field": cls._control_lock(
                key="amount_field",
                label="Amount",
                row=amount_field,
                read_at=read_at,
                image_width=image_width,
                image_height=image_height,
                role="amount",
            ),
            "buy_button": cls._control_lock(
                key="buy_button",
                label="BUY",
                row=buy,
                read_at=read_at,
                image_width=image_width,
                image_height=image_height,
                role="trade_button",
            ),
            "sell_button": cls._control_lock(
                key="sell_button",
                label="SELL",
                row=sell,
                read_at=read_at,
                image_width=image_width,
                image_height=image_height,
                role="trade_button",
            ),
            "popup_controls": {},
        }

    @staticmethod
    def _window_descriptor_from_hwnd(user32: Any, hwnd: int) -> dict[str, Any]:
        if int(hwnd or 0) <= 0:
            return {}
        try:
            import ctypes
            from ctypes import wintypes

            hwnd_value = wintypes.HWND(int(hwnd))
            is_window = getattr(user32, "IsWindow", None)
            if is_window is not None and not bool(is_window(hwnd_value)):
                return {}

            title = ""
            try:
                length = int(user32.GetWindowTextLengthW(hwnd_value))
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd_value, buffer, length + 1)
                    title = str(buffer.value or "").strip()
            except Exception:
                title = ""

            rect = wintypes.RECT()
            if not bool(user32.GetWindowRect(hwnd_value, ctypes.byref(rect))):
                return {}
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 1 or height < 1:
                return {}
            is_minimized = False
            try:
                is_minimized = bool(user32.IsIconic(hwnd_value))
            except Exception:
                is_minimized = False
            return {
                "hwnd": int(hwnd),
                "title": title,
                "bbox": [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)],
                "width": width,
                "height": height,
                "is_minimized": is_minimized,
            }
        except Exception:
            return {}

    @classmethod
    def _activate_locked_window_for_click(cls, user32: Any, hwnd: int) -> dict[str, Any]:
        if int(hwnd or 0) <= 0:
            return {}
        try:
            import ctypes
            from ctypes import wintypes

            hwnd_value = wintypes.HWND(int(hwnd))
            before = cls._window_descriptor_from_hwnd(user32, int(hwnd))
            if bool(before.get("is_minimized", False)):
                return before
            allow_set_foreground = getattr(user32, "AllowSetForegroundWindow", None)
            if allow_set_foreground is not None:
                try:
                    allow_set_foreground(-1)
                except Exception:
                    pass
            set_foreground = getattr(user32, "SetForegroundWindow", None)
            attach_thread_input = getattr(user32, "AttachThreadInput", None)
            get_window_thread = getattr(user32, "GetWindowThreadProcessId", None)
            get_foreground = getattr(user32, "GetForegroundWindow", None)
            kernel32 = getattr(ctypes, "windll", None)
            kernel32 = getattr(kernel32, "kernel32", None)
            current_thread_id = 0
            foreground_thread_id = 0
            target_thread_id = 0
            if kernel32 is not None:
                try:
                    current_thread_id = int(kernel32.GetCurrentThreadId() or 0)
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
            attached_threads: list[int] = []
            if attach_thread_input is not None and current_thread_id > 0:
                for thread_id in {foreground_thread_id, target_thread_id}:
                    if thread_id > 0 and thread_id != current_thread_id:
                        try:
                            if bool(attach_thread_input(current_thread_id, thread_id, True)):
                                attached_threads.append(thread_id)
                        except Exception:
                            pass
            if set_foreground is not None:
                try:
                    bool(set_foreground(hwnd_value))
                except Exception:
                    pass
            if attached_threads and attach_thread_input is not None and current_thread_id > 0:
                for thread_id in attached_threads:
                    try:
                        attach_thread_input(current_thread_id, thread_id, False)
                    except Exception:
                        pass
            time.sleep(0.16)
            return cls._window_descriptor_from_hwnd(user32, int(hwnd)) or before
        except Exception:
            return {}

    @staticmethod
    def _click_screen_point(
        user32: Any,
        x: int,
        y: int,
        *,
        expected_hwnd: int = 0,
        target_name: str = "",
        target_bbox: Sequence[int] | None = None,
        physical_point: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        ownership_point = physical_point if physical_point is not None else (int(x), int(y))
        diagnostic: dict[str, Any] = {
            "target_name": str(target_name or ""),
            "requested_cursor_point": [int(x), int(y)],
            "physical_point": [int(ownership_point[0]), int(ownership_point[1])],
            "target_screen_bbox": [int(value) for value in target_bbox] if target_bbox and len(target_bbox) >= 4 else [],
            "expected_hwnd": int(expected_hwnd or 0),
            "owned_by_expected_window": True,
            "cursor_landed_in_target": True,
            "sent_input": False,
        }
        if int(expected_hwnd or 0) > 0 and not PocketOptionBrokerExecutionBackend._screen_point_belongs_to_window(
            user32,
            int(expected_hwnd),
            int(ownership_point[0]),
            int(ownership_point[1]),
        ):
            diagnostic["owned_by_expected_window"] = False
            raise RuntimeError("Refusing broker click because the target point is not owned by the locked Pocket Option window.")
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.025)
        landed = (int(x), int(y))
        try:
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            point = POINT()
            get_cursor = getattr(user32, "GetCursorPos", None)
            if get_cursor is not None and bool(get_cursor(ctypes.byref(point))):
                landed = (int(point.x), int(point.y))
        except Exception:
            landed = (int(x), int(y))
        diagnostic["landed_cursor_point"] = [int(landed[0]), int(landed[1])]
        target = diagnostic["target_screen_bbox"]
        if target:
            tolerance = 4
            in_target = bool(
                int(target[0]) - tolerance <= int(landed[0]) <= int(target[2]) + tolerance
                and int(target[1]) - tolerance <= int(landed[1]) <= int(target[3]) + tolerance
            )
            diagnostic["cursor_landed_in_target"] = in_target
            if not in_target:
                raise RuntimeError(
                    f"Refusing broker click because the cursor did not land inside the locked {target_name or 'control'} box."
                )
        if int(expected_hwnd or 0) > 0 and not PocketOptionBrokerExecutionBackend._screen_point_belongs_to_window(
            user32,
            int(expected_hwnd),
            int(landed[0]),
            int(landed[1]),
        ):
            diagnostic["owned_by_expected_window"] = False
            raise RuntimeError("Refusing broker click because the landed cursor point is not owned by the locked Pocket Option window.")
        try:
            import ctypes
            from ctypes import wintypes

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", wintypes.LONG),
                    ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class INPUT_UNION(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]

            send_input = getattr(user32, "SendInput", None)
            if send_input is not None:
                extra = ctypes.c_ulong(0)
                for flags in (0x0002, 0x0004):
                    row = INPUT(
                        type=0,
                        u=INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, flags, 0, ctypes.pointer(extra))),
                    )
                    sent = int(send_input(1, ctypes.byref(row), ctypes.sizeof(INPUT)))
                    if sent != 1:
                        raise RuntimeError("SendInput did not report a mouse event.")
                    time.sleep(0.025)
                diagnostic["sent_input"] = True
                diagnostic["method"] = "SendInput"
                return diagnostic
        except Exception:
            pass
        mouseeventf_leftdown = 0x0002
        mouseeventf_leftup = 0x0004
        user32.mouse_event(mouseeventf_leftdown, 0, 0, 0, 0)
        time.sleep(0.025)
        user32.mouse_event(mouseeventf_leftup, 0, 0, 0, 0)
        diagnostic["sent_input"] = True
        diagnostic["method"] = "mouse_event"
        return diagnostic

    @staticmethod
    def _screen_point_belongs_to_window(user32: Any, expected_hwnd: int, x: int, y: int) -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            actual = int(user32.WindowFromPoint(POINT(int(x), int(y))) or 0)
            if actual <= 0:
                return False
            root = int(user32.GetAncestor(wintypes.HWND(actual), 2) or 0)
            if root == int(expected_hwnd):
                return True
            probe = actual
            while probe:
                if int(probe) == int(expected_hwnd):
                    return True
                probe = int(user32.GetParent(wintypes.HWND(probe)) or 0)
        except Exception:
            return False
        return False

    @staticmethod
    def _cursor_point_from_physical_screen_point(user32: Any, hwnd: int, x: int, y: int) -> tuple[int, int]:
        try:
            screen_width = int(user32.GetSystemMetrics(0))
            screen_height = int(user32.GetSystemMetrics(1))
            if screen_width > 0 and screen_height > 0 and int(x) <= screen_width + 32 and int(y) <= screen_height + 32:
                return int(x), int(y)
            dpi_reader = getattr(user32, "GetDpiForWindow", None)
            if dpi_reader is not None and int(hwnd or 0) > 0:
                dpi = float(dpi_reader(int(hwnd)) or 96.0)
                if dpi > 1.0 and abs(dpi - 96.0) > 0.01:
                    scale = dpi / 96.0
                    return int(round(float(x) / scale)), int(round(float(y) / scale))
        except Exception:
            pass
        return int(x), int(y)

    @staticmethod
    def _screen_bbox_from_local(window_left: int, window_top: int, bbox: Any) -> list[int]:
        local = PocketOptionBrokerExecutionBackend._normalized_bbox(bbox)
        if not local:
            return []
        return [
            int(window_left + local[0]),
            int(window_top + local[1]),
            int(window_left + local[2]),
            int(window_top + local[3]),
        ]

    def _set_expiry_with_popup(
        self,
        user32: Any,
        *,
        hwnd: int,
        descriptor: Mapping[str, Any] | None = None,
        window_left: int,
        window_top: int,
        time_field: Mapping[str, Any],
        expiry_seconds: int,
        current_seconds: int | None = None,
    ) -> dict[str, Any]:
        time_point = self._bbox_center(time_field.get("bbox", []))
        if time_point is None:
            raise RuntimeError("Expiry/time field center is missing.")
        clicks: list[dict[str, Any]] = []
        target_total = self._coerce_expiry_seconds(expiry_seconds)
        current_total = self._coerce_expiry_seconds(current_seconds) if current_seconds is not None and int(current_seconds) >= 0 else None
        if current_total is not None and current_total == target_total:
            return {
                "status": "verified",
                "clicks": clicks,
                "verification": {
                    "status": "verified",
                    "matches": True,
                    "target_seconds": int(target_total),
                    "visible_seconds": int(current_total),
                    "visible_text": self._format_expiry_text(current_total),
                    "confidence": 1.0,
                    "source": "pre_existing_expiry_lock",
                },
                "geometry": {"source": "pre_existing_expiry_lock"},
                "popup_locks": {},
            }

        def keyboard_marker(name: str, pause: float = 0.10) -> None:
            clicks.append({"name": name, "method": "keyboard"})
            time.sleep(max(0.01, float(pause)))

        def click_local(
            name: str,
            point: tuple[int, int],
            pause: float = 0.14,
            *,
            target_bbox: Any = None,
        ) -> dict[str, Any]:
            physical = (int(window_left + point[0]), int(window_top + point[1]))
            cursor = self._cursor_point_from_physical_screen_point(user32, hwnd, physical[0], physical[1])
            target_screen_bbox = self._screen_bbox_from_local(window_left, window_top, target_bbox)
            diagnostic = self._click_screen_point(
                user32,
                cursor[0],
                cursor[1],
                expected_hwnd=hwnd,
                target_name=name,
                target_bbox=target_screen_bbox,
                physical_point=physical,
            )
            row = {
                "name": name,
                "cursor_point": [int(cursor[0]), int(cursor[1])],
                "physical_point": [int(physical[0]), int(physical[1])],
                "diagnostic": diagnostic,
            }
            if target_screen_bbox:
                row["target_screen_bbox"] = target_screen_bbox
            clicks.append(row)
            time.sleep(max(0.01, float(pause)))
            return row

        controls = self._expiry_popup_control_points(time_field)
        popup_locks: dict[str, Any] = {}
        geometry: dict[str, Any] = {"source": "visual_popup_required"}
        
        # ENSURE WINDOW IS LOCKED AND FOCUSED FOR ENTIRE POPUP INTERACTION
        self._activate_locked_window_for_click(user32, hwnd)
        time.sleep(0.16)
        
        try:
            self._send_escape(user32)
            keyboard_marker("dismiss_existing_time_popup", pause=0.25)
        except Exception:
            LOGGER.debug("Unable to dismiss existing time popup by keyboard.", exc_info=True)
        
        # CLICK TO OPEN POPUP
        click_local("open_time_popup", time_point, pause=0.55, target_bbox=time_field.get("bbox", []))
        
        # WAIT FOR POPUP TO FULLY RENDER
        time.sleep(0.45)
        self._activate_locked_window_for_click(user32, hwnd)
        time.sleep(0.18)

        # VISUAL LOCK - RETRY UP TO 3 TIMES IF POPUP NOT DETECTED
        visual_controls = None
        for popup_attempt in range(3):
            visual_controls = self._expiry_popup_visual_control_points(
                descriptor=descriptor,
                time_field=time_field,
                fallback=controls,
            )
            if visual_controls:
                break
            if popup_attempt < 2:
                time.sleep(0.32)
                click_local(f"reopen_time_popup_attempt_{popup_attempt + 1}", time_point, pause=0.42, target_bbox=time_field.get("bbox", []))
                time.sleep(0.45)
                self._activate_locked_window_for_click(user32, hwnd)
                time.sleep(0.18)
        
        if not visual_controls:
            return {
                "status": "blocked",
                "message": "Expiry popup controls were not visually locked after 3 attempts; live execution will not click guessed stepper positions.",
                "clicks": clicks,
                "verification": {
                    "status": "unavailable",
                    "matches": False,
                    "target_seconds": int(target_total),
                    "message": "Popup visual lock unavailable after retries.",
                },
                "geometry": geometry,
                "popup_locks": {},
            }
        
        controls = dict(controls, **_mapping_to_dict(visual_controls.get("controls", {})))
        popup_locks = _mapping_to_dict(visual_controls.get("execution_boxes", {}))
        geometry = _mapping_to_dict(visual_controls.get("geometry", {})) or geometry

        # EXECUTE CLICK PLAN WITH STRICT VERIFICATION
        click_plan = self._expiry_popup_click_plan(current_total, target_total)
        for step_index, name in enumerate(click_plan):
            if name not in controls:
                return {
                    "status": "blocked",
                    "message": f"Expiry popup control '{name}' (step {step_index + 1}/{len(click_plan)}) was not visually locked.",
                    "clicks": clicks,
                    "verification": {
                        "status": "unavailable",
                        "matches": False,
                        "target_seconds": int(target_total),
                        "message": f"Popup control {name} missing.",
                    },
                    "geometry": geometry,
                    "popup_locks": popup_locks,
                }
            lock = _mapping_to_dict(popup_locks.get(name, {}))
            
            # LOCK WINDOW BEFORE EACH CLICK
            self._activate_locked_window_for_click(user32, hwnd)
            time.sleep(0.08)
            
            click_local(
                name,
                controls[name],
                pause=0.22 if name.endswith(("_plus", "_minus")) else 0.32,
                target_bbox=lock.get("bbox", []),
            )
            
            # WAIT AFTER STEPPER CLICKS FOR ANIMATION
            if name.endswith(("_plus", "_minus")):
                time.sleep(0.12)

        # VERIFY AND RETRY IF NEEDED
        verification = self._verify_expiry_popup_target(
            descriptor=descriptor,
            time_field=time_field,
            target_seconds=target_total,
        )
        
        # ENHANCED RETRY WITH UP TO 2 MORE ATTEMPTS
        retry_count = 0
        while not bool(verification.get("matches", False)) and retry_count < 2:
            verified_seconds = verification.get("visible_seconds", None)
            if not isinstance(verified_seconds, int) or verified_seconds < 0:
                break
                
            retry_plan = self._expiry_popup_click_plan(int(verified_seconds), target_total)
            if not retry_plan:
                break
            
            retry_count += 1
            time.sleep(0.25)
            self._activate_locked_window_for_click(user32, hwnd)
            time.sleep(0.10)
            
            for retry_name in retry_plan:
                if retry_name not in controls:
                    break
                lock = _mapping_to_dict(popup_locks.get(retry_name, {}))
                self._activate_locked_window_for_click(user32, hwnd)
                time.sleep(0.08)
                click_local(
                    f"{retry_name}_retry_{retry_count}",
                    controls[retry_name],
                    pause=0.24 if retry_name.endswith(("_plus", "_minus")) else 0.34,
                    target_bbox=lock.get("bbox", []),
                )
                if retry_name.endswith(("_plus", "_minus")):
                    time.sleep(0.14)
            
            time.sleep(0.35)
            verification = self._verify_expiry_popup_target(
                descriptor=descriptor,
                time_field=time_field,
                target_seconds=target_total,
            )
        
        # CLOSE POPUP
        time.sleep(0.18)
        self._activate_locked_window_for_click(user32, hwnd)
        time.sleep(0.10)
        try:
            self._send_escape(user32)
            keyboard_marker("close_time_popup", pause=0.28)
        except Exception:
            LOGGER.debug("Unable to close time popup by keyboard.", exc_info=True)
        
        # WAIT FOR POPUP TO FULLY DISMISS
        time.sleep(0.32)
        
        return {
            "status": "verified" if bool(verification.get("matches", False)) else "mismatch",
            "clicks": clicks,
            "verification": verification,
            "geometry": geometry,
            "popup_locks": popup_locks,
        }

    @staticmethod
    def _expiry_popup_click_plan(current_seconds: int | None, target_seconds: Any) -> list[str]:
        # Pocket Option exposes the popup as hour/minute/second steppers plus
        # shortcut buttons. Use exact HH:MM:SS state transitions; shortcuts are
        # allowed only when they equal the target or are the shortest safe anchor
        # before exact steppers finish the adjustment.
        target_total = PocketOptionBrokerExecutionBackend._coerce_expiry_seconds(target_seconds)
        target_hours, target_remainder = divmod(target_total, 3600)
        target_minutes = int(target_remainder // 60)
        target_second_value = int(target_remainder % 60)
        current_total = PocketOptionBrokerExecutionBackend._coerce_expiry_seconds(current_seconds) if current_seconds is not None else None
        if current_total == target_total:
            return []

        quick_targets = {
            3: "quick_s3",
            15: "quick_s15",
            30: "quick_s30",
            60: "quick_m1",
            180: "quick_m3",
            300: "quick_m5",
            1800: "quick_m30",
            3600: "quick_h1",
            14400: "quick_h4",
        }
        shortcut = quick_targets.get(target_total)
        if shortcut:
            return [shortcut]

        def axis_plan(from_seconds: int) -> list[str]:
            from_hours, from_remainder = divmod(
                PocketOptionBrokerExecutionBackend._coerce_expiry_seconds(from_seconds),
                3600,
            )
            from_minutes = int(from_remainder // 60)
            from_second_value = int(from_remainder % 60)
            steps: list[str] = []
            for axis, current, target in (
                ("hour", min(24, int(from_hours)), min(24, int(target_hours))),
                ("minute", min(59, int(from_minutes)), min(59, int(target_minutes))),
                ("second", min(59, int(from_second_value)), min(59, int(target_second_value))),
            ):
                delta = int(target) - int(current)
                if delta > 0:
                    steps.extend([f"{axis}_plus"] * delta)
                elif delta < 0:
                    steps.extend([f"{axis}_minus"] * abs(delta))
            return steps

        candidates: list[list[str]] = []
        if current_total is not None:
            candidates.append(axis_plan(current_total))

        for anchor_seconds, anchor_name in quick_targets.items():
            if anchor_seconds == target_total:
                continue
            candidates.append([anchor_name, *axis_plan(anchor_seconds)])

        if not candidates:
            return axis_plan(0)
        candidates.sort(key=lambda row: (len(row), 0 if row and row[0].startswith("quick_m") else 1, row))
        return candidates[0]

    def _expiry_popup_visual_control_points(
        self,
        *,
        descriptor: Mapping[str, Any] | None,
        time_field: Mapping[str, Any],
        fallback: Mapping[str, tuple[int, int]],
    ) -> dict[str, Any]:
        if descriptor is None:
            return {}
        bbox = cast(Sequence[Any], time_field.get("bbox", []))
        if len(bbox) < 4:
            return {}
        try:
            x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
            field_w = max(80, int(x1 - x0))
            field_h = max(28, int(y1 - y0))
            snapshot = WindowsWindowCaptureBackend().capture_window(descriptor).convert("RGB")
            import cv2  # type: ignore[import-not-found]

            arr = np.asarray(snapshot, dtype=np.uint8)
            height, width = int(arr.shape[0]), int(arr.shape[1])
            left = max(0, x0 - max(280, int(round(field_w * 1.95))))
            right = min(width, x0 + int(round(field_w * 0.40)))
            top = max(0, y1 + max(24, int(round(field_h * 0.42))))
            bottom = min(height, y1 + max(210, int(round(field_h * 4.25))))
            if right - left < 90 or bottom - top < 80:
                return {}
            roi = arr[top:bottom, left:right]
            hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
            rgb = roi.astype(np.int16)
            red = rgb[:, :, 0]
            green = rgb[:, :, 1]
            blue = rgb[:, :, 2]
            value = hsv[:, :, 2]
            saturation = hsv[:, :, 1]
            blue_text = (blue >= 88) & (green >= 58) & (blue >= red + 4) & (value >= 78)
            light_text = (value >= 118) & (saturation <= 110)
            mask = np.where(blue_text | light_text, 255, 0).astype(np.uint8)
            kernel = np.ones((2, 3), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
            mask = cv2.dilate(mask, kernel, iterations=1)
            contours, _hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            boxes: list[tuple[int, int, int, int]] = []
            for contour in contours:
                cx, cy, box_w, box_h = cv2.boundingRect(contour)
                area = int(box_w * box_h)
                if area < 8 or box_w < 3 or box_h < 5:
                    continue
                if box_w > max(80, int(field_w * 0.70)) or box_h > 34:
                    continue
                abs_x0 = int(left + cx)
                abs_y0 = int(top + cy)
                abs_x1 = int(abs_x0 + box_w)
                abs_y1 = int(abs_y0 + box_h)
                if abs_x1 < x0 - max(250, int(field_w * 1.8)) or abs_x0 > x0 + int(field_w * 0.35):
                    continue
                boxes.append((abs_x0, abs_y0, abs_x1, abs_y1))
            if len(boxes) < 5:
                return {}

            def cluster_axis(values: Sequence[float], *, tolerance: float) -> list[float]:
                clusters: list[list[float]] = []
                for value in sorted(float(item) for item in values):
                    if not clusters or abs(value - (sum(clusters[-1]) / max(1, len(clusters[-1])))) > tolerance:
                        clusters.append([value])
                    else:
                        clusters[-1].append(value)
                return [float(sum(group) / max(1, len(group))) for group in clusters if group]

            centers = [((x_a + x_b) * 0.5, (y_a + y_b) * 0.5) for x_a, y_a, x_b, y_b in boxes]
            row_candidates = cluster_axis(
                [point[1] for point in centers],
                tolerance=max(9.0, min(18.0, field_h * 0.26)),
            )
            shortcut_rows = [
                row
                for row in row_candidates
                if row >= y1 + max(20.0, field_h * 0.32)
                and row <= y1 + max(220.0, field_h * 4.30)
            ]
            if len(shortcut_rows) < 3:
                return {}
            shortcut_rows = sorted(shortcut_rows)[:3]
            row_band = max(12.0, min(24.0, field_h * 0.32))
            row_centers = [
                point
                for point in centers
                if any(abs(point[1] - row) <= row_band for row in shortcut_rows)
            ]
            col_candidates = cluster_axis(
                [point[0] for point in row_centers],
                tolerance=max(18.0, min(34.0, field_w * 0.22)),
            )
            col_candidates = [
                col
                for col in col_candidates
                if col >= x0 - max(270.0, field_w * 1.85)
                and col <= x0 + max(18.0, field_w * 0.30)
            ]
            if len(col_candidates) < 3:
                return {}
            cols = sorted(col_candidates)[-3:]
            if cols[-1] - cols[0] < max(48.0, field_w * 0.34):
                return {}
            rows = sorted(shortcut_rows)
            row_gap = float(np.median(np.diff(np.asarray(rows, dtype=np.float32)))) if len(rows) >= 2 else max(28.0, field_h * 0.55)
            plus_y = int(round(rows[0] - row_gap * 2.75))
            minus_y = int(round(rows[0] - row_gap * 1.10))
            plus_y = max(y0 + 8, min(y1 + int(round(field_h * 0.80)), plus_y))
            minus_y = max(plus_y + 22, min(int(round(rows[0] - 10)), minus_y))
            hour_x, minute_x, second_x = [int(round(value)) for value in cols[:3]]
            control_w = max(30, int(round(field_w * 0.36)))
            step_h = max(20, int(round(field_h * 0.42)))
            shortcut_w = max(44, int(round(field_w * 0.40)))
            shortcut_h = max(22, int(round(field_h * 0.43)))

            def center_bbox(cx: int, cy: int, box_w: int, box_h: int) -> list[int]:
                return [
                    max(0, int(round(cx - box_w * 0.5))),
                    max(0, int(round(cy - box_h * 0.5))),
                    min(width, int(round(cx + box_w * 0.5))),
                    min(height, int(round(cy + box_h * 0.5))),
                ]

            controls = {
                "hour_plus": (hour_x, plus_y),
                "minute_plus": (minute_x, plus_y),
                "second_plus": (second_x, plus_y),
                "hour_minus": (hour_x, minus_y),
                "minute_minus": (minute_x, minus_y),
                "second_minus": (second_x, minus_y),
                "quick_s3": (hour_x, int(round(rows[0]))),
                "quick_s15": (minute_x, int(round(rows[0]))),
                "quick_s30": (second_x, int(round(rows[0]))),
                "quick_m1": (hour_x, int(round(rows[1]))),
                "quick_m3": (minute_x, int(round(rows[1]))),
                "quick_m5": (second_x, int(round(rows[1]))),
                "quick_m30": (hour_x, int(round(rows[2]))),
                "quick_h1": (minute_x, int(round(rows[2]))),
                "quick_h4": (second_x, int(round(rows[2]))),
            }
            execution_boxes = {}
            read_at = _now_iso()
            for name, (cx, cy) in controls.items():
                is_shortcut = name.startswith("quick_")
                bbox = center_bbox(cx, cy, shortcut_w if is_shortcut else control_w, shortcut_h if is_shortcut else step_h)
                execution_boxes[name] = self._control_lock(
                    key=name,
                    label=name.replace("_", " ").upper(),
                    row={
                        "bbox": bbox,
                        "confidence": 0.76 if is_shortcut else 0.68,
                        "source": "visual_popup_shortcut_grid" if is_shortcut else "visual_popup_stepper_grid",
                    },
                    read_at=read_at,
                    image_width=width,
                    image_height=height,
                    role="expiry_popup_shortcut" if is_shortcut else "expiry_popup_stepper",
                )
            return {
                "controls": controls,
                "execution_boxes": execution_boxes,
                "geometry": {
                    "source": "visual_popup_shortcut_grid",
                    "search_bbox": [int(left), int(top), int(right), int(bottom)],
                    "columns": [int(round(value)) for value in cols],
                    "rows": [int(round(value)) for value in rows],
                    "plus_y": int(plus_y),
                    "minus_y": int(minus_y),
                    "component_count": int(len(boxes)),
                },
            }
        except Exception:
            LOGGER.debug("Expiry popup visual calibration failed; falling back to field-relative controls.", exc_info=True)
            _ = fallback
            return {}

    def _verify_expiry_popup_target(
        self,
        *,
        descriptor: Mapping[str, Any] | None,
        time_field: Mapping[str, Any],
        target_seconds: int,
    ) -> dict[str, Any]:
        if descriptor is None:
            return {"status": "unavailable", "message": "No window descriptor was available for expiry verification."}
        try:
            time.sleep(0.12)
            snapshot = WindowsWindowCaptureBackend().capture_window(descriptor).convert("RGB")
            read = self._read_expiry_time_text(snapshot, time_field)
            visible_seconds = int(read.get("seconds", 0) or 0)
            visible_text = str(read.get("text", "") or "")
            matches = bool(visible_seconds > 0 and abs(visible_seconds - int(target_seconds)) <= 2)
            return {
                "status": "verified" if matches else "mismatch",
                "matches": matches,
                "target_seconds": int(target_seconds),
                "visible_seconds": visible_seconds,
                "visible_text": visible_text,
                "confidence": _clip01(read.get("confidence", 0.0)),
                "source": str(read.get("source", "") or "time_field_ocr"),
            }
        except Exception as exc:
            LOGGER.debug("Expiry verification after popup adjustment failed.", exc_info=True)
            return {
                "status": "unavailable",
                "matches": False,
                "target_seconds": int(target_seconds),
                "message": str(exc),
            }

    def _verify_trade_click_result(
        self,
        *,
        descriptor: Mapping[str, Any] | None,
        side: str,
        button_bbox: Any,
        expiry_seconds: int,
    ) -> dict[str, Any]:
        if descriptor is None:
            return {
                "status": "unavailable",
                "confirmed": False,
                "message": "No window descriptor was available for post-click broker confirmation.",
            }
        try:
            time.sleep(0.35)
            snapshot = WindowsWindowCaptureBackend().capture_window(descriptor).convert("RGB")
            surface = self.read_surface(snapshot)
            side_action = _upper_action(side)
            post_button = _mapping_to_dict(surface.get("buy_button" if side_action == "BUY" else "sell_button", {}))
            post_bbox = self._normalized_bbox(post_button.get("bbox", []))
            original_bbox = self._normalized_bbox(button_bbox)
            # A confirmed trade requires a broker-side visual cue. The current
            # detector deliberately does not treat "input was sent" as success;
            # if the same button remains visible with no accepted-trade cue, the
            # result stays unverified for operator review and retry backoff.
            changed = bool(post_bbox and original_bbox and post_bbox != original_bbox)
            confirmed = False
            return {
                "status": "confirmed" if confirmed else "unverified",
                "confirmed": confirmed,
                "side": side_action,
                "expiry_seconds": int(expiry_seconds),
                "post_click_controls_ready": bool(surface.get("controls_ready", False)),
                "post_click_button_bbox": post_bbox,
                "button_bbox_changed": changed,
                "message": (
                    "Broker accepted-trade confirmation cue detected."
                    if confirmed
                    else "No broker accepted-trade visual cue was detected after the click."
                ),
            }
        except Exception as exc:
            LOGGER.debug("Post-click broker confirmation was unavailable.", exc_info=True)
            return {
                "status": "unavailable",
                "confirmed": False,
                "side": _upper_action(side),
                "expiry_seconds": int(expiry_seconds),
                "message": str(exc),
            }

    @staticmethod
    def _expiry_popup_control_points(time_field: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
        bbox = cast(Sequence[Any], time_field.get("bbox", []))
        if len(bbox) < 4:
            raise RuntimeError("Expiry/time field bounding box is missing.")
        x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
        field_w = max(80, int(x1 - x0))
        field_h = max(28, int(y1 - y0))
        hour_x = x0 - int(round(field_w * 1.30))
        minute_x = x0 - int(round(field_w * 0.86))
        second_x = x0 - int(round(field_w * 0.42))
        plus_y = y0 + int(round(field_h * 0.74))
        minus_y = y0 + int(round(field_h * 1.67))
        quick_row_1_y = y0 + int(round(field_h * 2.55))
        quick_row_2_y = y0 + int(round(field_h * 3.35))
        quick_row_3_y = y0 + int(round(field_h * 4.15))
        return {
            "hour_plus": (hour_x, plus_y),
            "minute_plus": (minute_x, plus_y),
            "second_plus": (second_x, plus_y),
            "hour_minus": (hour_x, minus_y),
            "minute_minus": (minute_x, minus_y),
            "second_minus": (second_x, minus_y),
            "quick_s3": (hour_x, quick_row_1_y),
            "quick_s15": (minute_x, quick_row_1_y),
            "quick_s30": (second_x, quick_row_1_y),
            "quick_m1": (hour_x, quick_row_2_y),
            "quick_m3": (minute_x, quick_row_2_y),
            "quick_m5": (second_x, quick_row_2_y),
            "quick_m30": (hour_x, quick_row_3_y),
            "quick_h1": (minute_x, quick_row_3_y),
            "quick_h4": (second_x, quick_row_3_y),
        }

    @staticmethod
    def _expiry_popup_dismiss_point(time_field: Mapping[str, Any]) -> tuple[int, int]:
        bbox = cast(Sequence[Any], time_field.get("bbox", []))
        if len(bbox) < 4:
            raise RuntimeError("Expiry/time field bounding box is missing.")
        x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
        field_w = max(80, int(x1 - x0))
        field_h = max(28, int(y1 - y0))
        return max(16, x0 - int(round(field_w * 3.0))), max(16, y0 + int(round(field_h * 0.70)))

    def _read_expiry_time_text(self, image: Image.Image, time_field: Mapping[str, Any]) -> dict[str, Any]:
        bbox = cast(Sequence[Any], time_field.get("bbox", []))
        if len(bbox) < 4:
            return {}
        try:
            import cv2  # type: ignore[import-not-found]

            x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
            arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
            height, width = int(arr.shape[0]), int(arr.shape[1])
            x0 = max(0, min(width - 1, x0))
            x1 = max(x0 + 1, min(width, x1))
            y0 = max(0, min(height - 1, y0))
            y1 = max(y0 + 1, min(height, y1))
            roi = arr[y0:y1, x0:x1]
            if roi.size == 0:
                return {}
            hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
            gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
            mask = np.where(
                ((hsv[:, :, 1] <= 120) & (hsv[:, :, 2] >= 120)) | (gray >= 130),
                255,
                0,
            ).astype(np.uint8)
            # The field also contains the clock icon and can include the "Time"
            # label when derived from surrounding controls. Keep the lower-left
            # text band, then split wide connected components into digit cells.
            digit_mask = mask.copy()
            digit_mask[: int(round(digit_mask.shape[0] * 0.28)), :] = 0
            digit_mask[int(round(digit_mask.shape[0] * 0.88)) :, :] = 0
            digit_mask[:, int(round(digit_mask.shape[1] * 0.76)) :] = 0
            template_fallback = _read_expiry_text_by_template(digit_mask)
            contours, _hier = cv2.findContours(digit_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            components: list[tuple[int, int, int, int]] = []
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
                return template_fallback
            components.sort(key=lambda row: row[0])
            if len(components) > 6:
                components = components[:6]
            scorer = self._identity_reader()
            digit_map = {"O": "0", "D": "0", "I": "1", "L": "1", "B": "3", "S": "5"}
            digits: list[str] = []
            confidences: list[float] = []
            for cx0, cy0, cx1, cy1 in components[:6]:
                crop = digit_mask[max(0, cy0 - 1): min(digit_mask.shape[0], cy1 + 1), max(0, cx0 - 1): min(digit_mask.shape[1], cx1 + 1)]
                label, confidence = scorer._score_ocr_character((crop > 0).astype(np.uint8))  # noqa: SLF001
                normalized = str(label or "").upper()
                normalized = digit_map.get(normalized, normalized)
                if not normalized.isdigit():
                    return template_fallback
                digits.append(normalized)
                confidences.append(_clip01(confidence))
            raw = "".join(digits)
            if len(raw) != 6:
                return template_fallback
            hours = int(raw[0:2])
            minutes = int(raw[2:4])
            seconds = int(raw[4:6])
            if hours > 24 or minutes > 59 or seconds > 59:
                return template_fallback
            confidence = float(np.mean(np.asarray(confidences, dtype=np.float32))) if confidences else 0.0
            parsed = {
                "text": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
                "seconds": int(hours * 3600 + minutes * 60 + seconds),
                "confidence": _clip01(confidence),
                "source": "time_field_ocr",
            }
            if _clip01(confidence) < 0.42 and template_fallback:
                return template_fallback
            return parsed
        except Exception:
            LOGGER.debug("Expiry time OCR failed.", exc_info=True)
            return {}

    @staticmethod
    def _send_ctrl_a(user32: Any) -> None:
        keyeventf_keyup = 0x0002
        vk_control = 0x11
        vk_a = 0x41
        user32.keybd_event(vk_control, 0, 0, 0)
        user32.keybd_event(vk_a, 0, 0, 0)
        user32.keybd_event(vk_a, 0, keyeventf_keyup, 0)
        user32.keybd_event(vk_control, 0, keyeventf_keyup, 0)

    @staticmethod
    def _send_text(user32: Any, text: str) -> None:
        keyeventf_keyup = 0x0002
        vk_shift = 0x10
        vk_control = 0x11
        vk_menu = 0x12
        for char in str(text or ""):
            scan = int(user32.VkKeyScanW(ord(char)))
            if scan == -1:
                continue
            vk = int(scan & 0xFF)
            shift_state = int((scan >> 8) & 0xFF)
            if vk <= 0:
                continue
            if shift_state & 1:
                user32.keybd_event(vk_shift, 0, 0, 0)
            if shift_state & 2:
                user32.keybd_event(vk_control, 0, 0, 0)
            if shift_state & 4:
                user32.keybd_event(vk_menu, 0, 0, 0)
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.018)
            user32.keybd_event(vk, 0, keyeventf_keyup, 0)
            if shift_state & 4:
                user32.keybd_event(vk_menu, 0, keyeventf_keyup, 0)
            if shift_state & 2:
                user32.keybd_event(vk_control, 0, keyeventf_keyup, 0)
            if shift_state & 1:
                user32.keybd_event(vk_shift, 0, keyeventf_keyup, 0)

    @staticmethod
    def _send_enter(user32: Any) -> None:
        keyeventf_keyup = 0x0002
        vk_return = 0x0D
        user32.keybd_event(vk_return, 0, 0, 0)
        time.sleep(0.018)
        user32.keybd_event(vk_return, 0, keyeventf_keyup, 0)

    @staticmethod
    def _send_escape(user32: Any) -> None:
        keyeventf_keyup = 0x0002
        vk_escape = 0x1B
        user32.keybd_event(vk_escape, 0, 0, 0)
        time.sleep(0.018)
        user32.keybd_event(vk_escape, 0, keyeventf_keyup, 0)

    @staticmethod
    def _coerce_expiry_seconds(value: Any) -> int:
        if isinstance(value, str):
            text = value.strip()
            match = re.fullmatch(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", text)
            if match:
                hours = int(match.group(1))
                minutes = int(match.group(2))
                seconds = int(match.group(3) or 0)
                if minutes > 59 or seconds > 59:
                    raise ValueError(f"Invalid expiry time: {value}")
                return max(0, min(24 * 3600, hours * 3600 + minutes * 60 + seconds))
        try:
            seconds_value = int(round(float(value)))
        except (TypeError, ValueError):
            seconds_value = _EXECUTION_DEFAULT_EXPIRY_SEC
        return max(0, min(24 * 3600, seconds_value))

    @classmethod
    def _format_expiry_text(cls, expiry_seconds: Any) -> str:
        total_seconds = cls._coerce_expiry_seconds(expiry_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @classmethod
    def _split_expiry_seconds(cls, expiry_seconds: Any) -> tuple[int, int, int]:
        total_seconds = cls._coerce_expiry_seconds(expiry_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return int(hours), int(minutes), int(seconds)

    def _find_button(self, arr: ArrayND, side: str) -> dict[str, Any]:
        height, width = int(arr.shape[0]), int(arr.shape[1])
        try:
            import cv2  # type: ignore[import-not-found]

            hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
            if side == "BUY":
                mask = np.where(
                    (hsv[:, :, 0] >= 36)
                    & (hsv[:, :, 0] <= 92)
                    & (hsv[:, :, 1] >= 72)
                    & (hsv[:, :, 2] >= 92),
                    255,
                    0,
                ).astype(np.uint8)
            else:
                mask = np.where(
                    ((hsv[:, :, 0] <= 8) | (hsv[:, :, 0] >= 168))
                    & (hsv[:, :, 1] >= 96)
                    & (hsv[:, :, 2] >= 118),
                    255,
                    0,
                ).astype(np.uint8)
            mask[:, : int(round(width * 0.42))] = 0
            kernel = np.ones((5, 5), dtype=np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            candidates: list[dict[str, Any]] = []
            for contour in contours:
                x, y, box_w, box_h = cv2.boundingRect(contour)
                area = int(box_w * box_h)
                if area < max(260, int(width * height * 0.003)):
                    continue
                if box_w < max(56, int(width * 0.040)) or box_h < max(22, int(height * 0.032)):
                    continue
                if box_w > int(width * 0.55) or box_h > int(height * 0.24):
                    continue
                aspect = float(box_w) / float(max(1, box_h))
                if aspect < 1.45 or aspect > 7.5:
                    continue
                density = float(np.mean(mask[y: y + box_h, x: x + box_w] > 0))
                if density < 0.38:
                    continue
                x_anchor = _clip01((x + box_w * 0.5 - width * 0.42) / max(1.0, width * 0.58))
                confidence = _clip01(0.54 * density + 0.24 * x_anchor + 0.22 * min(1.0, area / max(1.0, width * height * 0.055)))
                candidates.append(
                    {
                        "side": side,
                        "bbox": [int(x), int(y), int(x + box_w), int(y + box_h)],
                        "confidence": confidence,
                        "source": "color_button",
                    }
                )
            if candidates:
                candidates.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
                return dict(candidates[0])
        except Exception:
            pass
        return {}

    @staticmethod
    def _derive_amount_field(width: int, height: int, buy: Mapping[str, Any], sell: Mapping[str, Any]) -> dict[str, Any]:
        if not buy:
            return {}
        buy_bbox = cast(Sequence[Any], buy.get("bbox", []))
        sell_bbox = cast(Sequence[Any], sell.get("bbox", []))
        if len(buy_bbox) < 4:
            return {}
        x0 = int(round(float(buy_bbox[0])))
        x1 = int(round(float(buy_bbox[2])))
        if len(sell_bbox) >= 4:
            x0 = min(x0, int(round(float(sell_bbox[0]))))
            x1 = max(x1, int(round(float(sell_bbox[2]))))
        y0 = int(round(float(buy_bbox[1])))
        button_h = max(24, int(round(float(buy_bbox[3]) - float(buy_bbox[1]))))
        top = max(0, y0 - int(round(button_h * 2.65)))
        bottom = max(top + 18, y0 - int(round(button_h * 1.45)))
        bottom = min(height, bottom)
        return {
            "bbox": [max(0, x0), max(0, top), min(width, x1), max(1, bottom)],
            "confidence": 0.62,
            "source": "button_relative",
        }

    @staticmethod
    def _derive_time_field(width: int, height: int, amount_field: Mapping[str, Any]) -> dict[str, Any]:
        bbox = cast(Sequence[Any], amount_field.get("bbox", []))
        if len(bbox) < 4:
            return {}
        x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
        field_h = max(20, y1 - y0)
        top = max(0, y0 - int(round(field_h * 1.25)))
        bottom = max(top + 18, y0 - int(round(field_h * 0.25)))
        return {
            "bbox": [max(0, x0), max(0, top), min(width, x1), min(height, bottom)],
            "confidence": 0.48,
            "source": "amount_relative",
        }

    @staticmethod
    def _derive_order_panel(
        width: int,
        height: int,
        buy: Mapping[str, Any],
        sell: Mapping[str, Any],
        amount_field: Mapping[str, Any],
        time_field: Mapping[str, Any],
    ) -> dict[str, Any]:
        boxes: list[Sequence[Any]] = []
        for row in (buy, sell, amount_field, time_field):
            bbox = cast(Sequence[Any], row.get("bbox", []))
            if len(bbox) >= 4:
                boxes.append(bbox)
        if not boxes:
            return {}
        x0 = max(0, int(round(min(float(box[0]) for box in boxes) - 12)))
        y0 = max(0, int(round(min(float(box[1]) for box in boxes) - 42)))
        x1 = min(width, int(round(max(float(box[2]) for box in boxes) + 12)))
        y1 = min(height, int(round(max(float(box[3]) for box in boxes) + 42)))
        return {
            "bbox": [x0, y0, x1, y1],
            "confidence": 0.78,
            "source": "gui_controls_cluster",
        }


class PhoenixGuardWindowTrackingAdapter:
    @cached_property
    def _timeframe_template_bank(self) -> dict[str, list[ArrayND]]:
        try:
            import cv2  # type: ignore[import-not-found]
        except Exception:
            return {}

        bank: dict[str, list[ArrayND]] = {}
        fonts = (
            cv2.FONT_HERSHEY_SIMPLEX,
            cv2.FONT_HERSHEY_DUPLEX,
            cv2.FONT_HERSHEY_COMPLEX_SMALL,
            cv2.FONT_HERSHEY_TRIPLEX,
        )
        font_scales = (0.66, 0.78, 0.90, 1.02, 1.16, 1.30, 1.42)
        thicknesses = (1, 2, 3)
        for label in _TIMEFRAME_LABELS:
            variants: list[ArrayND] = []
            for font in fonts:
                for font_scale in font_scales:
                    for thickness in thicknesses:
                        (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
                        canvas = np.zeros(
                            (
                                max(36, text_height + baseline + 18),
                                max(60, text_width + 24),
                            ),
                            dtype=np.uint8,
                        )
                        origin = (
                            max(8, (canvas.shape[1] - text_width) // 2),
                            max(text_height + 6, (canvas.shape[0] + text_height) // 2 - baseline // 2),
                        )
                        cv2.putText(canvas, label, origin, font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
                        bbox = _binary_content_bbox(canvas)
                        if bbox is None:
                            continue
                        x0, y0, x1, y1 = bbox
                        cropped = canvas[max(0, y0 - 2): min(canvas.shape[0], y1 + 2), max(0, x0 - 2): min(canvas.shape[1], x1 + 2)]
                        if cropped.size == 0:
                            continue
                        variants.append((cropped > 0).astype(np.uint8))
            bank[label] = variants
        return bank

    @cached_property
    def _ocr_char_template_bank(self) -> dict[str, list[ArrayND]]:
        try:
            import cv2  # type: ignore[import-not-found]
        except Exception:
            return {}

        bank: dict[str, list[ArrayND]] = {}
        fonts = (
            cv2.FONT_HERSHEY_SIMPLEX,
            cv2.FONT_HERSHEY_DUPLEX,
            cv2.FONT_HERSHEY_COMPLEX_SMALL,
            cv2.FONT_HERSHEY_TRIPLEX,
        )
        font_scales = (0.58, 0.72, 0.86, 1.00, 1.18)
        thicknesses = (1, 2, 3)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/.-"
        for label in alphabet:
            variants: list[ArrayND] = []
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
                        if cropped.size == 0:
                            continue
                        variants.append((cropped > 0).astype(np.uint8))
            bank[label] = variants
        return bank

    @staticmethod
    def _normalize_binary_mask(mask: ArrayND) -> ArrayND:
        if mask.ndim != 2 or mask.size == 0:
            return np.zeros((1, 1), dtype=np.uint8)
        return (mask > 0).astype(np.uint8)

    def _score_ocr_character(self, mask: ArrayND) -> tuple[str, float]:
        normalized = self._normalize_binary_mask(mask)
        if normalized.size == 0 or int(np.sum(normalized > 0)) < 8:
            return "", 0.0
        try:
            import cv2  # type: ignore[import-not-found]
        except Exception:
            return "", 0.0

        best_label = ""
        best_score = 0.0
        second_best = 0.0
        for label, templates in self._ocr_char_template_bank.items():
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
        return best_label, _clip01(0.74 * best_score + 0.36 * margin)

    def _extract_market_text_mask(self, candidate_image: Image.Image) -> ArrayND:
        arr = np.asarray(candidate_image.convert("RGB"), dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[0] < 8 or arr.shape[1] < 8:
            return np.zeros((1, 1), dtype=np.uint8)
        try:
            import cv2  # type: ignore[import-not-found]

            hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            white_mask = np.where(
                ((hsv[:, :, 1] <= 108) & (hsv[:, :, 2] >= 168))
                | (gray >= 178),
                255,
                0,
            ).astype(np.uint8)
            kernel = np.ones((2, 2), dtype=np.uint8)
            white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        except Exception:
            gray = np.asarray(candidate_image.convert("L"), dtype=np.uint8)
            white_mask = np.where(gray >= 178, 255, 0).astype(np.uint8)
        return white_mask

    def _normalize_market_candidate(self, text: str) -> str:
        return _normalize_fx_market_candidate(text)

    def _detect_market_selector(
        self,
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if arr.ndim != 3:
            return {}
        height, width = int(arr.shape[0]), int(arr.shape[1])
        if height < 80 or width < 160:
            return {}

        timeframe_bbox = cast(Sequence[Any], _mapping_to_dict(timeframe_selector).get("bbox", []))
        roi_x1 = min(width, max(180, int(round(width * 0.58))))
        if len(timeframe_bbox) >= 4:
            try:
                timeframe_left = int(round(float(timeframe_bbox[0])))
                if timeframe_left > 96:
                    roi_x1 = min(roi_x1, max(120, timeframe_left - 8))
            except (TypeError, ValueError):
                pass
        roi_y1 = min(height, max(54, int(round(height * 0.19))))
        roi = arr[:roi_y1, :roi_x1]
        if roi.size == 0:
            return {}

        mask = self._extract_market_text_mask(Image.fromarray(roi, mode="RGB"))
        try:
            import cv2  # type: ignore[import-not-found]
        except Exception:
            return {}

        contours, _hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        components: list[dict[str, Any]] = []
        for contour in contours:
            x, y, box_w, box_h = cv2.boundingRect(contour)
            area = int(box_w * box_h)
            if area < 24 or box_w < 3 or box_h < 7:
                continue
            if box_h > roi.shape[0] * 0.86 or box_w > roi.shape[1] * 0.24:
                continue
            components.append({"bbox": [int(x), int(y), int(x + box_w), int(y + box_h)]})
        if len(components) < 3:
            return {}

        components.sort(key=lambda item: int(cast(Sequence[Any], item["bbox"])[0]))
        median_height = float(
            np.median(
                np.asarray(
                    [
                        max(1, int(cast(Sequence[Any], row["bbox"])[3]) - int(cast(Sequence[Any], row["bbox"])[1]))
                        for row in components
                    ],
                    dtype=np.float32,
                )
            )
        )
        filtered: list[dict[str, Any]] = []
        for row in components:
            bbox = cast(Sequence[Any], row["bbox"])
            height_value = max(1, int(bbox[3]) - int(bbox[1]))
            if height_value < max(7.0, median_height * 0.55):
                continue
            filtered.append(row)
        if len(filtered) < 3:
            filtered = components
        filtered.sort(key=lambda item: int(cast(Sequence[Any], item["bbox"])[0]))

        average_width = float(
            np.mean(
                np.asarray(
                    [
                        max(1, int(cast(Sequence[Any], row["bbox"])[2]) - int(cast(Sequence[Any], row["bbox"])[0]))
                        for row in filtered
                    ],
                    dtype=np.float32,
                )
            )
        )
        recognized_parts: list[str] = []
        confidences: list[float] = []
        previous_right = -1
        overall_bbox = [width, height, 0, 0]
        for row in filtered:
            bbox = cast(Sequence[Any], row["bbox"])
            x0, y0, x1, y1 = [int(value) for value in bbox[:4]]
            crop = mask[max(0, y0 - 1): min(mask.shape[0], y1 + 1), max(0, x0 - 1): min(mask.shape[1], x1 + 1)]
            label, confidence = self._score_ocr_character(crop)
            if not label:
                continue
            if previous_right >= 0 and (x0 - previous_right) >= max(6.0, average_width * 1.10):
                recognized_parts.append(" ")
            recognized_parts.append(label)
            confidences.append(confidence)
            previous_right = x1
            overall_bbox[0] = min(overall_bbox[0], x0)
            overall_bbox[1] = min(overall_bbox[1], y0)
            overall_bbox[2] = max(overall_bbox[2], x1)
            overall_bbox[3] = max(overall_bbox[3], y1)

        raw_text = "".join(recognized_parts).strip()
        normalized = self._normalize_market_candidate(raw_text)
        if not normalized:
            return {}
        char_confidence = float(np.mean(np.asarray(confidences, dtype=np.float32))) if confidences else 0.0
        left_anchor_score = _clip01(1.0 - (float(overall_bbox[0]) / max(roi.shape[1], 1)) * 1.8)
        confidence = _clip01(0.78 * char_confidence + 0.22 * left_anchor_score)
        if confidence < 0.48:
            return {}
        return {
            "value": normalized,
            "confidence": confidence,
            "bbox": [int(value) for value in overall_bbox],
            "source": "header_text",
            "raw_text": raw_text,
        }

    def study(self, image: Image.Image, *, session_payload: Mapping[str, Any] | None = None) -> TrackingStudy:
        surface = image.convert("RGB")
        timeframe_selector = self._detect_timeframe_selector(surface)
        market_selector = self._detect_market_selector(surface, timeframe_selector=timeframe_selector)
        chart_bbox, chart_confidence = self._detect_chart_bbox(surface)
        chart_region = _pixel_bbox_meta(surface.size, chart_bbox)
        chart_region["confidence"] = chart_confidence
        chart_crop_box = (
            int(chart_bbox[0]),
            int(chart_bbox[1]),
            int(chart_bbox[2]),
            int(chart_bbox[3]),
        )
        chart_image = surface.crop(chart_crop_box).convert("RGB")
        tracked_candles = self._extract_candle_tracks(chart_image)
        tracking_summary, latest_signal = self._build_signal_payloads(
            chart_image,
            chart_region,
            tracked_candles,
            timeframe_selector,
            market_selector=market_selector,
            session_payload=session_payload,
        )
        overlay_image = self._render_overlay(surface, chart_bbox, tracking_summary, latest_signal)
        tracking_summary["chart_region"] = chart_region
        tracking_summary["display_region"] = chart_region
        return TrackingStudy(
            chart_image=surface,
            overlay_image=overlay_image,
            chart_region=chart_region,
            tracking_summary=tracking_summary,
            latest_signal=latest_signal,
        )

    def _get_phoenixguard_memory_bank(self) -> Any | None:
        bank_dir = Path(getattr(RUNTIME, "memory_bank_dir", Path("memory_bank")))
        return _load_phoenixguard_memory_bank(bank_dir)

    def _phoenixguard_direction(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> str:
        projection = _mapping_to_dict(tracking_summary.get("projection", {}))
        for candidate in (
            projection.get("direction"),
            latest_signal.get("execution_action"),
            latest_signal.get("candidate_action"),
            latest_signal.get("action"),
            tracking_summary.get("local_direction"),
            tracking_summary.get("global_direction"),
        ):
            side = _upper_action(candidate, fallback="")
            if side in {"BUY", "SELL"}:
                return side
        return "HOLD"

    def _phoenixguard_local_phase(
        self,
        *,
        direction: str,
        global_direction: str,
        local_direction: str,
        current_state: str,
        consolidation_score: float,
    ) -> str:
        current = str(current_state or "").lower()
        with_trend = direction in {_upper_action(global_direction), _upper_action(local_direction)}
        if "reversal" in current or "exhaustion" in current:
            return "reversal_base"
        if "compression" in current or _clip01(consolidation_score) >= 0.62:
            return "with_trend_pause" if with_trend else "counter_trend_pullback"
        if "pullback" in current:
            return "with_trend_pause" if with_trend else "counter_trend_pullback"
        if with_trend and ("continuation" in current or direction == _upper_action(local_direction)):
            return "with_trend_push"
        if with_trend:
            return "continuation_base"
        return "counter_trend_pullback"

    def _phoenixguard_box_type(
        self,
        *,
        local_phase: str,
        current_state: str,
        consolidation_score: float,
    ) -> str:
        current = str(current_state or "").lower()
        if local_phase == "reversal_base":
            return "reversal_base"
        if "compression" in current or _clip01(consolidation_score) >= 0.62:
            return "compression_box"
        if "pullback" in current:
            return "pullback_retest"
        if "continuation" in current or local_phase == "with_trend_push":
            return "continuation_channel"
        return "transition_box"

    def _phoenixguard_decision_state(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> str:
        decision_kernel = _mapping_to_dict(tracking_summary.get("decision_kernel", {}))
        behavior = _mapping_to_dict(tracking_summary.get("behavior", {}))
        kernel_state = _upper_action(decision_kernel.get("state"), fallback="IDLE")
        entry_state = _upper_action(
            latest_signal.get("entry_state", tracking_summary.get("entry_state", "WAIT")),
            fallback="WAIT",
        )
        behavior_state = str(behavior.get("current_state", "") or "").lower()
        confidence = _clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", 0.0)))
        p_trigger_next_3 = _clip01(decision_kernel.get("p_trigger_next_3", 0.0))
        next_candle_bias = _upper_action(decision_kernel.get("next_candle_bias"), fallback="HOLD")
        dominant_side = _upper_action(decision_kernel.get("dominant_side"), fallback="HOLD")

        if entry_state == "COMPLETE" or kernel_state == "COMPLETE" or bool(latest_signal.get("target_reached", False)):
            return "target_complete"
        if entry_state == "INVALIDATED" or kernel_state == "INVALIDATED":
            return "invalidated"
        if kernel_state == "STALE":
            return "late"
        if kernel_state == "ACTIVE":
            return "active"
        if kernel_state == "TRIGGERED" or entry_state in {"SNIPER_READY", "TRIGGER_READY"}:
            return "triggering" if bool(latest_signal.get("actionable")) else "armed"
        if "exhaustion" in behavior_state and kernel_state not in {"ACTIVE", "COMPLETE"}:
            return "exhausted"
        if kernel_state == "ARMED":
            return "armed"
        if "reversal" in behavior_state or "failed" in behavior_state:
            return "transition"
        if entry_state == "SNIPER_WATCH":
            return "building"
        if next_candle_bias in {"BUY", "SELL"} and dominant_side in {"BUY", "SELL"} and next_candle_bias != dominant_side:
            return "transition" if confidence >= 0.55 else "uncertain but maturing"
        if p_trigger_next_3 >= 0.68 or confidence >= 0.58:
            return "uncertain but maturing"
        return "forming"

    def _phoenixguard_opportunity_timing(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        *,
        decision_state: str,
    ) -> str:
        entry_state = _upper_action(
            latest_signal.get("entry_state", tracking_summary.get("entry_state", "WAIT")),
            fallback="WAIT",
        )
        if decision_state == "target_complete" or entry_state == "COMPLETE" or bool(latest_signal.get("target_reached", False)):
            return "complete"
        if decision_state in {"invalidated", "late", "exhausted"}:
            return "late"
        if entry_state in {"SNIPER_READY", "TRIGGER_READY"} and bool(latest_signal.get("actionable")):
            return "optimal"
        if entry_state in {"SNIPER_WATCH", "WAIT_FOR_SNIPER", "WAIT_FOR_TRIGGER", "WAIT"}:
            return "early"
        if decision_state in {"armed", "triggering", "active"}:
            return "on_time"
        return "early"

    def _phoenixguard_query_chart_state(
        self,
        chart_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        candles: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        projection: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("projection") or {})
        behavior: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("behavior") or {})
        decision_kernel: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("decision_kernel") or {})
        candle_statistics: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("candle_statistics") or {})
        chart_region: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("chart_region") or {})
        box_context: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("box_context") or {})
        control_state = _mapping_to_dict(
            tracking_summary.get("global_local_control", latest_signal.get("global_local_control", {}))
        )
        map_timing = _mapping_to_dict(tracking_summary.get("map_timing", latest_signal.get("map_timing", {})))
        support_resistance_zones = _sequence_of_mappings(tracking_summary.get("support_resistance_zones", []))
        direction = self._phoenixguard_direction(tracking_summary, latest_signal)
        global_direction = _upper_action(tracking_summary.get("global_direction"))
        local_direction = _upper_action(tracking_summary.get("local_direction"))
        impulse_direction = _upper_action(tracking_summary.get("impulse_direction"))
        current_state = str(behavior.get("current_state", "sideways_pause") or "sideways_pause")
        next_state = str(behavior.get("next_most_likely_state", "sideways_pause") or "sideways_pause")
        transition_probs = {
            str(key): _clip01(value)
            for key, value in _mapping_to_dict(behavior.get("next_state_probs", {})).items()
        }
        continuation_probability = float(
            sum(value for key, value in transition_probs.items() if "continuation" in key or "breakout_attempt" in key)
        )
        pullback_probability = float(sum(value for key, value in transition_probs.items() if "pullback" in key))
        reversal_probability = float(
            sum(value for key, value in transition_probs.items() if "reversal" in key or "confirmed_reversal" in key)
        )
        fakeout_probability = float(sum(value for key, value in transition_probs.items() if "failed_breakout" in key or "fakeout" in key))
        if continuation_probability + pullback_probability + reversal_probability + fakeout_probability <= 1e-6:
            continuation_probability = _clip01(tracking_summary.get("continuation_score", 0.0))
            pullback_probability = _clip01(_mapping_to_dict(behavior.get("box_context", {})).get("compression_score", 0.0))
            reversal_probability = _clip01(tracking_summary.get("reversal_score", 0.0))
            fakeout_probability = _clip01(0.5 * _mapping_to_dict(behavior.get("box_context", {})).get("failure_risk", 0.0))
        consolidation_score = _clip01(tracking_summary.get("consolidation_score", 0.0))
        local_phase = self._phoenixguard_local_phase(
            direction=direction,
            global_direction=global_direction,
            local_direction=local_direction,
            current_state=current_state,
            consolidation_score=consolidation_score,
        )
        box_type = self._phoenixguard_box_type(
            local_phase=local_phase,
            current_state=current_state,
            consolidation_score=consolidation_score,
        )
        entry_type = "reversal" if local_phase == "reversal_base" else "continuation"
        latest_token = {}
        tokens = _sequence_of_mappings(behavior.get("candle_tokens", []))
        if tokens:
            latest_token = dict(tokens[-1])
        latest_body_pct = _clip01(
            latest_token.get("body_pct", tracking_summary.get("latest_body_height_pct", candles[-1].get("body_height_pct", 0.0) if candles else 0.0))
        )
        upper_wick_pct = _clip01(latest_token.get("upper_wick_pct", 0.26))
        lower_wick_pct = _clip01(latest_token.get("lower_wick_pct", 0.26))
        nearest_support = self._phoenixguard_nearest_sr_zone(support_resistance_zones, role="support")
        nearest_resistance = self._phoenixguard_nearest_sr_zone(support_resistance_zones, role="resistance")
        wick_reaction_read = self._phoenixguard_wick_reaction_read(
            direction=direction,
            upper_wick_pct=upper_wick_pct,
            lower_wick_pct=lower_wick_pct,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
        )
        entry_windows = self._phoenixguard_entry_windows(tracking_summary, direction=direction)
        confidence = _clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", projection.get("confidence", 0.0))))
        sideways_probability = _clip01(projection.get("sideways_probability", 0.0))
        target_first_probability = _clip01(projection.get("target_first_probability", 0.0))
        invalidation_first_probability = _clip01(projection.get("invalidation_first_probability", 0.0))
        box_sequence_agreement = _clip01(
            (0.44 if direction in {global_direction, local_direction} else 0.16)
            + (0.28 if direction == local_direction else 0.0)
            + (0.18 if direction == global_direction else 0.0)
            + (0.10 if direction == impulse_direction else 0.0)
        )
        path_clarity = _clip01(
            0.42 * target_first_probability
            + 0.28 * _clip01(decision_kernel.get("p_trigger_next_3", 0.0))
            + 0.16 * _clip01(behavior.get("state_confidence", 0.0))
            + 0.14 * (1.0 - sideways_probability)
        )
        macro_trend = "BULL" if global_direction == "BUY" else "BEAR" if global_direction == "SELL" else ("BULL" if direction == "BUY" else "BEAR")
        sequence_state = {
            "continuation_probability": continuation_probability,
            "pullback_probability": pullback_probability,
            "reversal_probability": reversal_probability,
            "fakeout_probability": fakeout_probability,
            "path_clarity": path_clarity,
            "box_sequence_agreement": box_sequence_agreement,
            "recent_box_consolidation": consolidation_score,
            "sequence_model": {
                "buy_pressure": _clip01(decision_kernel.get("buy_evidence", 0.0)),
                "sell_pressure": _clip01(decision_kernel.get("sell_evidence", 0.0)),
                "continuation_readiness": _clip01(target_first_probability),
                "reversal_pressure": _clip01(reversal_probability),
                "history_coherence": _clip01(confidence),
                "uncertainty": _clip01(sideways_probability + invalidation_first_probability * 0.45),
            },
        }
        projected_next_box = {
            "direction": direction,
            "box_type": box_type,
            "confidence": _clip01(projection.get("confidence", confidence)),
            "dominance_gap": abs(_float_or(decision_kernel.get("net_bias", 0.0))),
            "explanation": str(projection.get("message", "") or latest_signal.get("summary", "")),
        }
        recent_tokens = [
            f"{_upper_action(token.get('direction'), fallback='HOLD')}:{_friendly_phrase(token.get('micro_structure_event', ''), fallback='state')}"
            for token in tokens[-5:]
        ]
        regression_rows = [
            _mapping_to_dict(item)
            for item in candles[-18:]
            if np.isfinite(_float_or(item.get("normalized_y", item.get("center_y", 0.0)), 0.0))
        ]
        regression_slope = 0.0
        if len(regression_rows) >= 2:
            y_values = np.asarray(
                [
                    _clip01(
                        row.get(
                            "normalized_y",
                            _float_or(row.get("center_y", 0.0), 0.0) / max(1.0, float(chart_image.height)),
                        )
                    )
                    for row in regression_rows
                ],
                dtype=np.float32,
            )
            x_values = np.linspace(0.0, 1.0, num=y_values.size, dtype=np.float32)
            try:
                regression_slope = float(np.polyfit(x_values, y_values, 1)[0])
            except Exception:
                regression_slope = float(y_values[-1] - y_values[0])
        if regression_slope < -0.014:
            regression_direction = "BUY"
        elif regression_slope > 0.014:
            regression_direction = "SELL"
        else:
            regression_direction = "HOLD"
        recent_buy_count = sum(1 for row in regression_rows if _upper_action(row.get("direction"), fallback="HOLD") == "BUY")
        recent_sell_count = sum(1 for row in regression_rows if _upper_action(row.get("direction"), fallback="HOLD") == "SELL")
        pressure_direction = "BUY" if recent_buy_count >= recent_sell_count else "SELL"
        candle_regression = {
            "slope": round(float(regression_slope), 4),
            "direction": regression_direction,
            "pressure_direction": pressure_direction,
            "confidence": round(float(_clip01(abs(regression_slope) * 3.8 + abs(recent_buy_count - recent_sell_count) / max(1, len(regression_rows)) * 0.38)), 4),
            "alignment_to_label": 1.0 if regression_direction == direction else (0.58 if regression_direction == "HOLD" else 0.18),
            "recent_activity_columns": int(len(regression_rows)),
        }
        chart_state = {
            "entry_type": entry_type,
            "direction": direction,
            "macro_trend": macro_trend,
            "candle_count_up": int(candle_statistics.get("buy_count", 0) or 0),
            "candle_count_down": int(candle_statistics.get("sell_count", 0) or 0),
            "consolidation_streak": int(round(consolidation_score * 6.0)),
            "consolidation_type": "tight" if consolidation_score >= 0.50 else "none",
            "entry_candle": {
                "body_pct": latest_body_pct,
                "upper_wick_pct": upper_wick_pct,
                "lower_wick_pct": lower_wick_pct,
                "color": "green" if direction == "BUY" else "red" if direction == "SELL" else "neutral",
            },
            "wick_reaction_read": wick_reaction_read,
            "pre_entry_sequence": recent_tokens,
            "reversal_signal": "wick_rejection" if local_phase == "reversal_base" or "rejection" in current_state or "exhaustion" in current_state else "none",
            "continuation_signal": "impulse_pause" if "continuation" in current_state or "pullback" in current_state or direction == local_direction else "none",
            "direction_probability": confidence,
            "momentum_bias": "bullish" if direction == "BUY" else "bearish" if direction == "SELL" else "neutral",
            "structure_setup": box_type,
            "projection_bias_direction": direction,
            "projection_bias_confidence": _clip01(projection.get("confidence", confidence)),
            "projection_dominance": _clip01(abs(_float_or(decision_kernel.get("net_bias", 0.0)))),
            "projected_next_box": projected_next_box,
            "projection_explanation": str(projection.get("message", "") or latest_signal.get("summary", "")),
            "memory_candle_regression": candle_regression,
            "swing_state": {
                "recent_swing_direction": local_direction,
                "macro_swing_direction": global_direction,
                "swing_phase": str(behavior.get("trend_phase", current_state) or current_state),
            },
            "timeframe": str(latest_signal.get("focus_timeframe", tracking_summary.get("detected_timeframe", "M5")) or "M5").upper(),
            "latest_parse_quality": _clip01(chart_region.get("confidence", 0.5)),
            "spacing_consistency": _clip01(0.56 + 0.28 * _clip01(candle_statistics.get("sample_weight", 0.0))),
            "recent_candle_count": int(tracking_summary.get("visible_candle_count", len(candles)) or len(candles)),
            "color_flip_rate": _clip01(candle_statistics.get("opposing_ratio", 0.0)),
            "continuation_probability": continuation_probability,
            "pullback_probability": pullback_probability,
            "reversal_probability": reversal_probability,
            "fakeout_probability": fakeout_probability,
            "path_clarity": path_clarity,
            "box_sequence_agreement": box_sequence_agreement,
            "sequence_buy_pressure": _clip01(decision_kernel.get("buy_evidence", 0.0)),
            "sequence_sell_pressure": _clip01(decision_kernel.get("sell_evidence", 0.0)),
            "continuation_readiness": _clip01(target_first_probability),
            "reversal_pressure": _clip01(reversal_probability),
            "history_coherence": _clip01(confidence),
            "sequence_uncertainty": _clip01(sideways_probability + invalidation_first_probability * 0.45),
            "support_strength": _clip01(0.55 * (1.0 if direction == "BUY" else 0.30) + 0.45 * _clip01(1.0 - invalidation_first_probability)),
            "resistance_strength": _clip01(0.55 * (1.0 if direction == "SELL" else 0.30) + 0.45 * _clip01(1.0 - invalidation_first_probability)),
            "support_resistance_zones": support_resistance_zones,
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "breakout_strength": _clip01(decision_kernel.get("p_trigger_next_1", 0.0)),
            "pullback_strength": _clip01(pullback_probability),
            "consolidation_score": consolidation_score,
            "structure_buy_pressure": _clip01(0.62 * _clip01(decision_kernel.get("buy_evidence", 0.0)) + 0.38 * (1.0 if global_direction == "BUY" else 0.0)),
            "structure_sell_pressure": _clip01(0.62 * _clip01(decision_kernel.get("sell_evidence", 0.0)) + 0.38 * (1.0 if global_direction == "SELL" else 0.0)),
            "structure_bias_confidence": _clip01(decision_kernel.get("bias_strength", confidence)),
            "raw_description": (
                f"{direction} {local_phase} | global={global_direction} local={local_direction} impulse={impulse_direction} "
                f"| state={current_state} next={next_state} | entry={latest_signal.get('entry_state', tracking_summary.get('entry_state', 'WAIT'))} "
                f"| control={control_state.get('owner', '--')}:{control_state.get('direction', '--')} "
                f"| wick={wick_reaction_read.get('message', '')} | recent={' ; '.join(recent_tokens)}"
            ),
            "global_local_control": control_state,
            "map_timing": map_timing,
            "entry_windows": entry_windows,
            "sequence_state": sequence_state,
            "grounded_structure": {
                "support_strength": _clip01(0.48 + 0.24 * _clip01(box_context.get("compression_score", 0.0))),
                "resistance_strength": _clip01(0.48 + 0.24 * _clip01(box_context.get("compression_score", 0.0))),
                "breakout_strength": _clip01(decision_kernel.get("p_trigger_next_1", 0.0)),
                "pullback_strength": _clip01(pullback_probability),
                "consolidation_strength": consolidation_score,
                "buy_pressure": _clip01(decision_kernel.get("buy_evidence", 0.0)),
                "sell_pressure": _clip01(decision_kernel.get("sell_evidence", 0.0)),
                "structure_bias_confidence": _clip01(decision_kernel.get("bias_strength", confidence)),
            },
        }
        entry_progression = derive_entry_progression_profile(chart_state, sequence_state=sequence_state)
        sequence_model = _mapping_to_dict(sequence_state.get("sequence_model", {}))
        sequence_model["progression_maturity"] = _clip01(entry_progression.get("maturity_score", 0.0))
        sequence_model["progression_velocity"] = _clip01(entry_progression.get("progression_velocity", 0.0))
        sequence_model["exhaustion_risk"] = _clip01(entry_progression.get("exhaustion_risk", 0.0))
        sequence_model["continuation_strength"] = _clip01(entry_progression.get("continuation_strength", 0.0))
        sequence_state["sequence_model"] = sequence_model
        sequence_state["entry_progression"] = dict(entry_progression)
        sequence_state["progression_stage"] = str(entry_progression.get("progression_stage", sequence_state.get("progression_stage", "progression")) or "progression")
        sequence_state["progression_velocity"] = _clip01(entry_progression.get("progression_velocity", 0.0))
        sequence_state["progression_maturity"] = _clip01(entry_progression.get("maturity_score", 0.0))
        sequence_state["progression_exhaustion"] = _clip01(entry_progression.get("exhaustion_risk", 0.0))
        chart_state["entry_progression"] = dict(entry_progression)
        style_signature = infer_style_signature_from_chart_state(chart_state)
        metric_profile = build_metric_profile(chart_state, sequence_state=sequence_state)
        query_embed = None
        bank = self._get_phoenixguard_memory_bank()
        if bank is not None:
            query_embed = bank.embed_description(chart_state, chart_image)
        trajectory_signature = build_trajectory_signature(
            chart_state,
            sequence_index=int(decision_kernel.get("setup_age_candles", candle_statistics.get("direction_run", 0)) or 0),
            sequence_state=sequence_state,
        )
        late_interaction_tokens = build_late_interaction_tokens(
            chart_state,
            combined_embed=query_embed.tolist() if isinstance(query_embed, np.ndarray) else None,
            style_signature=style_signature,
            sequence_state=sequence_state,
            metric_profile=metric_profile,
        )
        return {
            "direction": direction,
            "macro_trend": macro_trend,
            "local_phase": local_phase,
            "chart_state": chart_state,
            "query_embed": query_embed,
            "query_context": {
                "late_interaction_tokens": late_interaction_tokens,
                "trajectory_signature": trajectory_signature,
                "style_signature": style_signature,
                "metric_profile": metric_profile,
                "global_local_control": control_state,
                "map_timing": map_timing,
                "support_resistance_zones": support_resistance_zones,
                "nearest_support": nearest_support,
                "nearest_resistance": nearest_resistance,
                "wick_reaction_read": wick_reaction_read,
                "entry_windows": entry_windows,
            },
        }

    def _phoenixguard_nearest_sr_zone(
        self,
        zones: Sequence[Mapping[str, Any]],
        *,
        role: str,
    ) -> dict[str, Any]:
        wanted = str(role or "").lower()
        matches = [
            _mapping_to_dict(zone)
            for zone in zones
            if str(zone.get("role", zone.get("kind", "")) or "").lower() == wanted
        ]
        if not matches:
            return {}
        matches.sort(
            key=lambda zone: float(
                zone.get("distance_to_latest_norm", zone.get("distance_to_price", zone.get("distance", 9999.0))) or 9999.0
            )
        )
        zone = matches[0]
        return {
            "role": wanted,
            "label": str(zone.get("label", wanted) or wanted),
            "line_y": zone.get("line_y"),
            "bbox": zone.get("bbox"),
            "touches": int(zone.get("touches", zone.get("touch_count", 0)) or 0),
            "confidence": round(float(_clip01(zone.get("confidence", zone.get("strength", 0.0)))), 4),
            "distance_to_latest_norm": round(float(_clip01(zone.get("distance_to_latest_norm", 1.0))), 4),
        }

    def _phoenixguard_wick_reaction_read(
        self,
        *,
        direction: str,
        upper_wick_pct: float,
        lower_wick_pct: float,
        nearest_support: Mapping[str, Any],
        nearest_resistance: Mapping[str, Any],
    ) -> dict[str, Any]:
        upper = _clip01(upper_wick_pct)
        lower = _clip01(lower_wick_pct)
        dominant = "upper" if upper > lower + 0.04 else "lower" if lower > upper + 0.04 else "balanced"
        if direction == "BUY":
            reaction = "support_absorption" if dominant == "lower" else "resistance_rejection_risk" if dominant == "upper" else "balanced_confirmation"
            context = nearest_support if dominant != "upper" else nearest_resistance
            reaction_side = "BUY" if dominant != "upper" else "SELL"
        elif direction == "SELL":
            reaction = "resistance_rejection" if dominant == "upper" else "support_absorption_risk" if dominant == "lower" else "balanced_confirmation"
            context = nearest_resistance if dominant != "lower" else nearest_support
            reaction_side = "SELL" if dominant != "lower" else "BUY"
        else:
            reaction = "neutral_wick_read"
            context = nearest_support or nearest_resistance
            reaction_side = "HOLD"
        zone_label = str(_mapping_to_dict(context).get("label", "nearest zone") or "nearest zone")
        return {
            "dominant_wick": dominant,
            "upper_wick_pct": round(float(upper), 4),
            "lower_wick_pct": round(float(lower), 4),
            "reaction": reaction,
            "reaction_side": reaction_side,
            "message": f"{dominant} wick read as {reaction} around {zone_label}",
            "support_resistance_context": dict(context) if context else {},
        }

    def _phoenixguard_entry_windows(
        self,
        tracking_summary: Mapping[str, Any],
        *,
        direction: str,
    ) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        for source_name in ("structure_boxes", "historical_structure"):
            for index, source in enumerate(_sequence_of_mappings(tracking_summary.get(source_name, []))[:8]):
                plan = _mapping_to_dict(source.get("sniper_target_plan", {}))
                for role in ("sniper_window", "trigger_window", "target_window"):
                    bbox = source.get(role)
                    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)):
                        continue
                    windows.append(
                        {
                            "source": source_name,
                            "source_index": index,
                            "role": role.replace("_window", ""),
                            "direction": _upper_action(source.get("direction", direction), fallback=direction),
                            "bbox": list(bbox),
                            "invalidation_y": source.get("invalidation_y", plan.get("invalidation_y")),
                            "control_hold_candles": source.get("control_hold_candles", plan.get("control_hold_candles")),
                            "label": str(source.get("label", source.get("key", role)) or role),
                        }
                    )
        return windows

    def _phoenixguard_memory_findings(self, bank: Any | None) -> dict[str, Any]:
        if bank is None:
            return _default_phoenixguard_report(message="PhoenixGuard memory bank is unavailable.", status="degraded")["memory_findings"]
        entries = list(getattr(bank, "entries", []))
        if not entries:
            return _default_phoenixguard_report(message="PhoenixGuard memory bank is empty.", status="degraded")["memory_findings"]

        buy_entries = [entry for entry in entries if _upper_action(getattr(entry, "label", "")) == "BUY"]
        sell_entries = [entry for entry in entries if _upper_action(getattr(entry, "label", "")) == "SELL"]
        episode_sizes = Counter(str(getattr(entry, "episode_id", "") or f"{getattr(entry, 'label', 'HOLD')}:{getattr(entry, 'entry_id', '')}") for entry in entries)
        multi_image_episodes = sum(1 for size in episode_sizes.values() if int(size) > 1)

        def summarize(label_entries: Sequence[Any]) -> dict[str, Any]:
            total = len(label_entries)
            phase_counts = Counter(str(getattr(entry, "local_phase", "") or "unknown") for entry in label_entries)
            intent_counts = Counter(str(getattr(entry, "intent_next", "") or "continue") for entry in label_entries)
            reversal_like = sum(
                1
                for entry in label_entries
                if str(getattr(entry, "local_phase", "") or "") == "reversal_base"
                or str(getattr(entry, "intent_next", "") or "") == "reversal_attempt"
            )
            continuation_like = sum(1 for entry in label_entries if str(getattr(entry, "intent_next", "") or "") == "continue")
            pullback_like = sum(
                1
                for entry in label_entries
                if str(getattr(entry, "intent_next", "") or "") == "pullback"
                or "pullback" in str(getattr(entry, "local_phase", "") or "")
            )
            fakeout_like = sum(1 for entry in label_entries if str(getattr(entry, "intent_next", "") or "") == "fakeout")
            early = sum(1 for entry in label_entries if int(getattr(entry, "sequence_index", 0) or 0) == 0)
            late = max(0, total - early)
            upper_dominant = 0
            lower_dominant = 0
            wick_rejection = 0
            for entry in label_entries:
                chart_state = _mapping_to_dict(getattr(entry, "chart_state", {}))
                candle = _mapping_to_dict(chart_state.get("entry_candle", {}))
                upper = _clip01(candle.get("upper_wick_pct", 0.0))
                lower = _clip01(candle.get("lower_wick_pct", 0.0))
                if upper > lower + 0.03:
                    upper_dominant += 1
                if lower > upper + 0.03:
                    lower_dominant += 1
                if str(chart_state.get("reversal_signal", "none") or "none").lower() != "none":
                    wick_rejection += 1
            dominant_phase = phase_counts.most_common(1)[0][0] if phase_counts else "unknown"
            dominant_intent = intent_counts.most_common(1)[0][0] if intent_counts else "unknown"
            return {
                "total": total,
                "dominant_phase": dominant_phase,
                "dominant_intent": dominant_intent,
                "reversal_like": reversal_like,
                "continuation_like": continuation_like,
                "pullback_like": pullback_like,
                "fakeout_like": fakeout_like,
                "early": early,
                "late": late,
                "upper_dominant": upper_dominant,
                "lower_dominant": lower_dominant,
                "wick_rejection": wick_rejection,
            }

        buy_summary = summarize(buy_entries)
        sell_summary = summarize(sell_entries)
        reversal_count = sum(
            1
            for entry in entries
            if str(getattr(entry, "local_phase", "") or "") == "reversal_base"
            or str(getattr(entry, "intent_next", "") or "") == "reversal_attempt"
        )
        pullback_count = sum(
            1
            for entry in entries
            if str(getattr(entry, "intent_next", "") or "") == "pullback"
            or "pullback" in str(getattr(entry, "local_phase", "") or "")
        )
        continuation_count = sum(1 for entry in entries if str(getattr(entry, "intent_next", "") or "") == "continue")
        notes: list[str] = []
        if multi_image_episodes <= max(4, int(round(len(entries) * 0.02))):
            notes.append("Memory bank is still dominated by single-frame anchors; multi-image episode continuity remains sparse.")
        else:
            notes.append(f"{multi_image_episodes} multi-image memory episodes are available for sequence-aware matching.")
        return {
            "bank_ready": True,
            "total_entries": int(len(entries)),
            "buy_count": int(len(buy_entries)),
            "sell_count": int(len(sell_entries)),
            "episode_count": int(len(episode_sizes)),
            "multi_image_episodes": int(multi_image_episodes),
            "buys": (
                f"{buy_summary['total']} buy memories. Dominant phase is {_friendly_phrase(buy_summary['dominant_phase'])}; "
                f"reversal-like {buy_summary['reversal_like']}, continuation-like {buy_summary['continuation_like']}."
            ),
            "sells": (
                f"{sell_summary['total']} sell memories. Dominant phase is {_friendly_phrase(sell_summary['dominant_phase'])}; "
                f"reversal-like {sell_summary['reversal_like']}, continuation-like {sell_summary['continuation_like']}."
            ),
            "reversals": (
                f"{reversal_count} memories cluster around reversal-base behavior, usually with {_friendly_phrase(buy_summary['dominant_intent'])} / "
                f"{_friendly_phrase(sell_summary['dominant_intent'])} follow-through bias."
            ),
            "pullbacks": f"{pullback_count} memories behave like pullbacks or counter-trend pauses before the next decision leg.",
            "continuations": f"{continuation_count} memories continue in the original trend after pullback/compression release.",
            "wick_behavior": (
                f"Buy memory leans lower-wick absorption {buy_summary['lower_dominant']} vs upper-wick dominance {buy_summary['upper_dominant']}; "
                f"sell memory leans upper-wick rejection {sell_summary['upper_dominant']} vs lower-wick dominance {sell_summary['lower_dominant']}."
            ),
            "sequence_behavior": (
                f"Episode continuity spans {len(episode_sizes)} grouped memory threads; {multi_image_episodes} threads contain more than one image."
            ),
            "early_entries": (
                f"Early entries dominate the bank: {buy_summary['early'] + sell_summary['early']} first-touch memories sit at sequence index 0."
            ),
            "late_entries": (
                f"Later sequence captures remain limited: {buy_summary['late'] + sell_summary['late']} memories arrive after the first frame in a thread."
            ),
            "notes": notes,
        }

    def _phoenixguard_zone_text(self, zone: Mapping[str, Any], *, include_target: bool = False) -> str:
        bbox = cast(Sequence[Any], zone.get("bbox", []))
        if len(bbox) < 4:
            return ""
        label = _friendly_phrase(zone.get("label", zone.get("kind", "zone")), fallback="zone").upper()
        y0 = int(min(float(bbox[1]), float(bbox[3])))
        y1 = int(max(float(bbox[1]), float(bbox[3])))
        text = f"{label} y[{y0},{y1}]"
        target_bbox = cast(Sequence[Any], zone.get("target_bbox", []))
        if include_target and len(target_bbox) >= 4:
            target_y0 = int(min(float(target_bbox[1]), float(target_bbox[3])))
            target_y1 = int(max(float(target_bbox[1]), float(target_bbox[3])))
            text = f"{text} -> target y[{target_y0},{target_y1}]"
        invalidation_raw = zone.get("invalidation_y", None)
        try:
            if invalidation_raw is not None:
                text = f"{text} | invalidate @{int(round(float(invalidation_raw)))}"
        except (TypeError, ValueError):
            pass
        return text

    def _phoenixguard_current_market_structure(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> dict[str, Any]:
        projection = _mapping_to_dict(tracking_summary.get("projection", {}))
        behavior = _mapping_to_dict(tracking_summary.get("behavior", {}))
        decision_kernel = _mapping_to_dict(tracking_summary.get("decision_kernel", {}))
        candle_statistics = _mapping_to_dict(tracking_summary.get("candle_statistics", {}))
        control_state = _mapping_to_dict(
            tracking_summary.get("global_local_control", latest_signal.get("global_local_control", {}))
        )
        map_timing = _mapping_to_dict(tracking_summary.get("map_timing", latest_signal.get("map_timing", {})))
        support_resistance_zones = _sequence_of_mappings(tracking_summary.get("support_resistance_zones", []))
        direction = self._phoenixguard_direction(tracking_summary, latest_signal)
        global_direction = _upper_action(tracking_summary.get("global_direction"))
        local_direction = _upper_action(tracking_summary.get("local_direction"))
        impulse_direction = _upper_action(tracking_summary.get("impulse_direction"))
        current_state = _friendly_phrase(behavior.get("current_state", "sideways_pause"), fallback="sideways pause")
        next_state = _friendly_phrase(behavior.get("next_most_likely_state", "sideways_pause"), fallback="sideways pause")
        tokens = _sequence_of_mappings(behavior.get("candle_tokens", []))
        latest_token = dict(tokens[-1]) if tokens else {}
        upper_wick_pct = _clip01(latest_token.get("upper_wick_pct", 0.0))
        lower_wick_pct = _clip01(latest_token.get("lower_wick_pct", 0.0))
        nearest_support = self._phoenixguard_nearest_sr_zone(support_resistance_zones, role="support")
        nearest_resistance = self._phoenixguard_nearest_sr_zone(support_resistance_zones, role="resistance")
        wick_reaction_read = self._phoenixguard_wick_reaction_read(
            direction=direction,
            upper_wick_pct=upper_wick_pct,
            lower_wick_pct=lower_wick_pct,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
        )
        recent_events = [
            f"{_upper_action(token.get('direction'), fallback='HOLD')} {_friendly_phrase(token.get('micro_structure_event', ''), fallback='state')}"
            for token in tokens[-4:]
        ]
        zones = _sequence_of_mappings(projection.get("zones", []))
        primary_zone: dict[str, Any] = {}
        sniper_zone: dict[str, Any] = {}
        for zone in zones:
            kind = str(zone.get("kind", "")).lower()
            if kind == "primary" and not primary_zone:
                primary_zone = zone
            elif kind == "sniper" and not sniper_zone:
                sniper_zone = zone
        active_zones = {
            "sniper": self._phoenixguard_zone_text(sniper_zone),
            "trigger": self._phoenixguard_zone_text(primary_zone, include_target=True),
            "invalidation": "",
            "nearest_support": str(nearest_support.get("label", "")),
            "nearest_resistance": str(nearest_resistance.get("label", "")),
        }
        invalidation_raw = primary_zone.get("invalidation_y", sniper_zone.get("invalidation_y"))
        try:
            if invalidation_raw is not None:
                active_zones["invalidation"] = f"INVALIDATION @{int(round(float(invalidation_raw)))}"
        except (TypeError, ValueError):
            active_zones["invalidation"] = ""
        return {
            "global_structure": (
                f"{global_direction} bias | slope {float(tracking_summary.get('global_slope', 0.0) or 0.0):+.3f} | "
                f"timeframe {_friendly_phrase(latest_signal.get('focus_timeframe', tracking_summary.get('detected_timeframe', '')), fallback='--')}"
            ),
            "major_local_structure": (
                f"{local_direction} local structure | slope {float(tracking_summary.get('local_slope', 0.0) or 0.0):+.3f} | "
                f"projection {direction} {_friendly_phrase(projection.get('entry_state', tracking_summary.get('entry_state', 'WAIT')), fallback='WAIT')}"
            ),
            "nested_local_structure": (
                f"{current_state} | next {next_state} | sample {int(candle_statistics.get('sample_size', tracking_summary.get('visible_candle_count', 0)) or 0)} candles"
            ),
            "microstructure": " -> ".join(recent_events) if recent_events else "No microstructure tokens available yet.",
            "latest_candle_state": (
                f"{_upper_action(latest_token.get('direction'), fallback=_upper_action(tracking_summary.get('impulse_direction')))} candle | "
                f"{_friendly_phrase(latest_token.get('micro_structure_event', tracking_summary.get('latest_candle_color', 'unknown')), fallback='unknown')} | "
                f"upper wick {upper_wick_pct:.2f} lower wick {lower_wick_pct:.2f} | "
                f"trigger {float(latest_token.get('distance_to_trigger', 0.0) or 0.0):.2f} | sniper {float(latest_token.get('distance_to_sniper', 0.0) or 0.0):.2f}"
            ),
            "current_active_transition_state": (
                f"{current_state} -> {next_state} | {_friendly_phrase(decision_kernel.get('market_conversation', latest_signal.get('summary', '')), fallback='waiting')}"
            ),
            "state_stack": (
                f"GLOBAL {global_direction} > LOCAL {local_direction} > MICRO {impulse_direction} > "
                f"{_friendly_phrase(latest_signal.get('entry_state', tracking_summary.get('entry_state', 'WAIT')), fallback='WAIT')}"
            ),
            "control_summary": (
                f"{str(control_state.get('owner', 'unknown')).upper()} controls "
                f"{_upper_action(control_state.get('direction'), fallback='HOLD')} for "
                f"{int(_float_or(control_state.get('horizon_candles', control_state.get('control_horizon_candles', 0)), 0.0))} candles"
            ),
            "global_local_control": control_state,
            "map_timing": map_timing,
            "support_resistance_zones": support_resistance_zones,
            "wick_reaction_read": wick_reaction_read,
            "active_zones": active_zones,
        }

    def _phoenixguard_memory_match(
        self,
        bank: Any | None,
        results: Sequence[Any],
        transition_probs: Mapping[str, Any],
        *,
        local_phase: str,
        dominant_side: str,
        actionable: bool,
    ) -> dict[str, Any]:
        if bank is None or not results:
            note = "No memory matches were available for the current live structure."
            return {
                "dominant_memory_side": "HOLD",
                "top_matches": [],
                "how_current_differs": note,
                "historical_next_event": note,
                "historical_next_event_bias": {},
            }
        entry_map = {
            str(getattr(entry, "entry_id", "")): entry
            for entry in list(getattr(bank, "entries", []))
        }
        top_matches: list[dict[str, Any]] = []
        memory_labels: list[str] = []
        for result in results[:3]:
            entry = entry_map.get(str(getattr(result, "entry_id", "")))
            label = _upper_action(getattr(result, "label", ""), fallback="HOLD")
            memory_labels.append(label)
            top_matches.append(
                {
                    "entry_id": str(getattr(result, "entry_id", "")),
                    "label": label,
                    "similarity": round(float(getattr(result, "similarity", 0.0) or 0.0), 4),
                    "image_name": Path(str(getattr(entry, "image_path", getattr(result, "entry_id", "match")) or "match")).name,
                    "macro_trend": str(getattr(entry, "macro_trend", "") or ""),
                    "local_phase": str(getattr(entry, "local_phase", "") or ""),
                    "intent_next": str(getattr(entry, "intent_next", "") or ""),
                    "sequence_index": int(getattr(entry, "sequence_index", 0) or 0),
                    "episode_id": str(getattr(entry, "episode_id", "") or ""),
                }
            )
        dominant_memory_side = Counter(memory_labels).most_common(1)[0][0] if memory_labels else "HOLD"
        top_match = top_matches[0] if top_matches else {}
        differences: list[str] = []
        top_phase = str(top_match.get("local_phase", "") or "")
        top_intent = str(top_match.get("intent_next", "") or "")
        if dominant_memory_side != dominant_side and dominant_side in {"BUY", "SELL"}:
            differences.append(f"Closest memory side leans {dominant_memory_side} while live dominance is {dominant_side}.")
        if top_phase and top_phase != local_phase:
            differences.append(f"Closest memory phase is {_friendly_phrase(top_phase)} while live phase is {_friendly_phrase(local_phase)}.")
        if top_intent == "continue" and not actionable:
            differences.append("Current live sequence is still one step before execution; matching continuations historically confirmed after the rejection/reclaim leg.")
        if not differences:
            differences.append("Live structure aligns closely with the highest-ranked memory phase and side.")
        next_event = ""
        if transition_probs:
            next_event, next_prob = max(
                ((str(key), float(value)) for key, value in transition_probs.items()),
                key=lambda item: item[1],
            )
            historical_next_event = f"{_friendly_phrase(next_event)} dominated {next_prob:.0%} of the closest memory matches."
        else:
            historical_next_event = "Historical next-event bias was unavailable."
        return {
            "dominant_memory_side": dominant_memory_side,
            "top_matches": top_matches,
            "how_current_differs": " ".join(differences),
            "historical_next_event": historical_next_event,
            "historical_next_event_bias": {
                str(key): round(float(value), 4)
                for key, value in transition_probs.items()
            },
        }

    def _phoenixguard_forward_projection(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        memory_match: Mapping[str, Any],
        *,
        decision_state: str,
        opportunity_timing: str,
    ) -> dict[str, Any]:
        projection: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("projection") or {})
        decision_kernel: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("decision_kernel") or {})
        direction = self._phoenixguard_direction(tracking_summary, latest_signal)
        next_candle_bias = _upper_action(decision_kernel.get("next_candle_bias"), fallback="HOLD")
        zones = _sequence_of_mappings(projection.get("zones", []))
        sniper_zone: dict[str, Any] = {}
        primary_zone: dict[str, Any] = {}
        for zone in zones:
            kind = str(zone.get("kind", "")).lower()
            if kind == "sniper" and not sniper_zone:
                sniper_zone = zone
            elif kind == "primary" and not primary_zone:
                primary_zone = zone
        trigger_area = self._phoenixguard_zone_text(primary_zone, include_target=False)
        target_area = self._phoenixguard_zone_text(primary_zone, include_target=True)
        invalidation_raw = primary_zone.get("invalidation_y", sniper_zone.get("invalidation_y"))
        invalidation_area = ""
        try:
            if invalidation_raw is not None:
                invalidation_area = f"INVALIDATION @{int(round(float(invalidation_raw)))}"
        except (TypeError, ValueError):
            invalidation_area = ""
        eta_trigger = int(decision_kernel.get("eta_trigger_candles", 0) or 0)
        immediacy = "immediate" if bool(latest_signal.get("actionable")) or eta_trigger <= 1 else "near_term" if eta_trigger <= 3 else "delayed"
        if decision_state == "target_complete":
            expected_next_move = "Target zone is already reached. The prior move is complete; PhoenixGuard should monitor continuation or a short pullback reset."
            likely_path = "Target complete -> no fresh trigger -> monitor continuation/pullback -> wait for a new object box."
            immediacy = "complete"
        elif decision_state == "invalidated":
            expected_next_move = "Setup is invalidated. Price must rebuild a new structure before PhoenixGuard can project a fresh action path."
            likely_path = "Invalidation break -> no trade -> rebuild sequence."
        elif next_candle_bias in {"BUY", "SELL"} and next_candle_bias != direction and direction in {"BUY", "SELL"}:
            expected_next_move = f"Short {next_candle_bias.lower()} counter-candle first, then {direction.lower()} pressure is expected to reassert."
            likely_path = (
                f"{_friendly_phrase(sniper_zone.get('label', 'watch area'), fallback='watch area')} -> "
                f"{direction} rejection/reclaim -> {_friendly_phrase(primary_zone.get('label', 'trigger'), fallback='trigger')} -> target."
            )
        elif bool(latest_signal.get("actionable")):
            expected_next_move = f"{direction} continuation is already executable if the current confirmation holds."
            likely_path = f"Confirmed {direction} trigger -> target progression while invalidation stays untouched."
        else:
            expected_next_move = f"{direction} remains dominant, but PhoenixGuard is still waiting for the execution gate."
            likely_path = (
                f"Travel into {_friendly_phrase(sniper_zone.get('label', 'sniper watch'), fallback='sniper watch')} -> "
                f"reclaim/rejection -> {_friendly_phrase(primary_zone.get('label', 'trigger'), fallback='trigger')} -> target."
            )
        likely_reaction_points = [text for text in [self._phoenixguard_zone_text(sniper_zone), trigger_area, invalidation_area] if text]
        if not likely_reaction_points:
            likely_reaction_points = [str(memory_match.get("historical_next_event", "") or "").strip()]
        return {
            "dominant_side": direction,
            "expected_next_move": expected_next_move,
            "likely_path": likely_path,
            "likely_reaction_points": likely_reaction_points,
            "likely_trigger_area": trigger_area,
            "likely_target_area": target_area,
            "likely_invalidation_area": invalidation_area,
            "immediacy": immediacy,
            "opportunity_timing": opportunity_timing,
        }

    def _phoenixguard_timing_judgment(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        forward_projection: Mapping[str, Any],
        *,
        decision_state: str,
    ) -> dict[str, Any]:
        direction = self._phoenixguard_direction(tracking_summary, latest_signal)
        timing_signal = _mapping_to_dict(latest_signal.get("timing_signal", {}))
        decision_kernel = _mapping_to_dict(tracking_summary.get("decision_kernel", {}))
        actionable_now = bool(latest_signal.get("actionable")) and _upper_action(latest_signal.get("execution_action"), fallback="HOLD") in {"BUY", "SELL"}
        map_timing = _mapping_to_dict(tracking_summary.get("map_timing", latest_signal.get("map_timing", {})))
        must_happen_first = (
            "Target is already complete; wait for a new setup before any fresh trigger."
            if decision_state == "target_complete" or bool(map_timing.get("target_reached", False))
            else (
            "Execution gate is already open."
            if actionable_now
            else str(
                timing_signal.get(
                    "instruction",
                    latest_signal.get("summary", "Price still has to confirm inside the PhoenixGuard execution gate."),
                )
                or "Price still has to confirm inside the PhoenixGuard execution gate."
            )
            )
        )
        high_quality_conditions = (
            f"{direction} should align with global/local structure, touch the watch or trigger area, "
            "and print a reclaim/rejection or acceptance candle without immediately hitting invalidation."
        )
        weak_or_invalid_conditions = (
            "Weak if counter candles keep accepting against the projected side, "
            "or if the trigger zone is touched without follow-through. Invalid if the invalidation line breaks cleanly."
        )
        projection_timing = "on_time"
        still_on_time = True
        if decision_state == "target_complete":
            projection_timing = "complete"
            still_on_time = False
        elif decision_state in {"late", "invalidated"}:
            projection_timing = "late"
            still_on_time = False
        elif decision_state == "exhausted":
            projection_timing = "fragile"
        elif not actionable_now and int(decision_kernel.get("eta_trigger_candles", 0) or 0) > 3:
            projection_timing = "forming"
        return {
            "actionable_now": actionable_now,
            "must_happen_first": must_happen_first,
            "high_quality_conditions": high_quality_conditions,
            "weak_or_invalid_conditions": weak_or_invalid_conditions,
            "projection_timing": projection_timing,
            "still_on_time": still_on_time,
            "map_timing": map_timing,
        }

    def _phoenixguard_tracker_guidance(
        self,
        current_market_structure: Mapping[str, Any],
        memory_match: Mapping[str, Any],
        *,
        decision_state: str,
    ) -> dict[str, Any]:
        top_matches = cast(Sequence[Any], memory_match.get("top_matches", []))
        memory_panel = [str(_mapping_to_dict(match).get("image_name", "") or "") for match in top_matches[:3] if str(_mapping_to_dict(match).get("image_name", "") or "")]
        state_stack = str(current_market_structure.get("state_stack", "") or "")
        return {
            "state_stack": state_stack,
            "overlay_priority": [
                state_stack,
                f"Decision state: {decision_state}",
                "Render sniper -> trigger -> target -> invalidation in that order.",
            ],
            "memory_panel": memory_panel,
            "alert_conditions": [
                "Alert when decision state changes.",
                "Alert when next candle bias diverges from the dominant side.",
                "Alert when trigger is accepted or invalidation breaks.",
            ],
            "forward_projection_contract": (
                "Show the current stack, current decision state, the live execution gate, and the top memory analogs in the same frame."
            ),
        }

    def _execution_expiry_seconds(
        self,
        latest_signal: Mapping[str, Any],
        tracking_summary: Mapping[str, Any],
        *,
        lane: str,
    ) -> int:
        timeframe = str(latest_signal.get("focus_timeframe", tracking_summary.get("detected_timeframe", "M5")) or "M5").upper()
        timeframe_sec = _timeframe_seconds(timeframe, default=300)
        decision_kernel = _mapping_to_dict(tracking_summary.get("decision_kernel", latest_signal.get("decision_kernel", {})))
        hold_candles = max(1, int(decision_kernel.get("hold_for_candles", 1) or 1))
        target_eta = max(1, int(decision_kernel.get("eta_target_after_trigger_candles", hold_candles) or hold_candles))
        invalidation_eta = max(1, int(decision_kernel.get("eta_invalidation_candles", hold_candles + 1) or (hold_candles + 1)))
        target_before_invalidation = _clip01(decision_kernel.get("p_target_before_invalidation", 0.0))
        if str(lane or "").upper() == "COUNTERTREND_SCALP":
            hold_candles = 3
        else:
            if target_before_invalidation >= 0.58 and target_eta < invalidation_eta:
                hold_candles = max(hold_candles, target_eta)
            hold_candles = max(1, min(18, max(hold_candles, min(target_eta, max(1, invalidation_eta + 1)))))
        return int(max(_EXECUTION_MIN_LIVE_EXPIRY_SEC, timeframe_sec * hold_candles))

    def _build_phoenixguard_report(
        self,
        chart_image: Image.Image,
        candles: Sequence[Mapping[str, Any]],
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            bank = self._get_phoenixguard_memory_bank()
            report = _default_phoenixguard_report(message="PhoenixGuard live report warming.", status="warming")
            memory_findings = self._phoenixguard_memory_findings(bank)
            current_market_structure = self._phoenixguard_current_market_structure(tracking_summary, latest_signal)
            decision_state = self._phoenixguard_decision_state(tracking_summary, latest_signal)
            opportunity_timing = self._phoenixguard_opportunity_timing(
                tracking_summary,
                latest_signal,
                decision_state=decision_state,
            )

            query_context_payload = self._phoenixguard_query_chart_state(chart_image, tracking_summary, latest_signal, candles)
            transition_probs: dict[str, float] = {}
            results: list[Any] = []
            if bank is not None and query_context_payload.get("query_embed") is not None:
                entry_count = len(getattr(bank, "entries", []) or [])
                recall_top_k = max(5, min(64, entry_count if entry_count > 0 else 5))
                results = list(
                    bank.search(
                        query_context_payload["query_embed"],
                        top_k=recall_top_k,
                        macro_trend=str(query_context_payload["macro_trend"]),
                        local_phase=str(query_context_payload["local_phase"]),
                        query_context=cast(Mapping[str, Any], query_context_payload["query_context"]),
                    )
                )
                transition_probs = {
                    str(key): round(float(value), 4)
                    for key, value in bank.summarize_transition_probabilities(results).items()
                }
            memory_match = self._phoenixguard_memory_match(
                bank,
                results,
                transition_probs,
                local_phase=str(query_context_payload["local_phase"]),
                dominant_side=str(query_context_payload["direction"]),
                actionable=bool(latest_signal.get("actionable")),
            )
            forward_projection = self._phoenixguard_forward_projection(
                tracking_summary,
                latest_signal,
                memory_match,
                decision_state=decision_state,
                opportunity_timing=opportunity_timing,
            )
            timing_judgment = self._phoenixguard_timing_judgment(
                tracking_summary,
                latest_signal,
                forward_projection,
                decision_state=decision_state,
            )
            tracker_guidance = self._phoenixguard_tracker_guidance(
                current_market_structure,
                memory_match,
                decision_state=decision_state,
            )
            dominant_side = str(forward_projection.get("dominant_side", "HOLD") or "HOLD")
            headline = (
                f"{dominant_side} {decision_state}. "
                f"{str(forward_projection.get('expected_next_move', '') or '').strip()}"
            ).strip()
            report.update(
                {
                    "status": "ready" if bank is not None else "degraded",
                    "headline": headline,
                    "generated_at": _now_iso(),
                    "memory_findings": memory_findings,
                    "current_market_structure": current_market_structure,
                    "memory_to_current_match": memory_match,
                    "decision_state": decision_state,
                    "forward_projection": forward_projection,
                    "timing_judgment": timing_judgment,
                    "tracker_upgrade_guidance": tracker_guidance,
                }
            )
            if bank is None:
                notes = list(cast(Sequence[Any], report["memory_findings"].get("notes", [])))
                notes.append("Live tracker report is running without the historical memory bank.")
                report["memory_findings"]["notes"] = [str(note) for note in notes]
            return report
        except Exception as exc:
            LOGGER.exception("PhoenixGuard live report build failed: %s", exc)
            return _default_phoenixguard_report(
                message=f"PhoenixGuard live report failed: {exc}",
                status="error",
            )

    @staticmethod
    def _normalized_vector(values: Any) -> NDArray[np.float32]:
        vector = np.asarray(values, dtype=np.float32).reshape(-1)
        if vector.size == 0:
            return np.zeros((0,), dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-8:
            return vector.astype(np.float32)
        return (vector / norm).astype(np.float32)

    def _chart_image_from_tracking_summary(
        self,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
    ) -> Image.Image:
        chart_region = _mapping_to_dict(tracking_summary.get("chart_region", {}))
        bbox = cast(Sequence[Any], chart_region.get("pixel_bbox", []))
        if len(bbox) >= 4:
            clipped = _clip_bbox_to_image(surface_image.size, bbox)
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                cropped_box: tuple[int, int, int, int] = (clipped[0], clipped[1], clipped[2], clipped[3])
                return surface_image.crop(cropped_box).convert("RGB")
        return surface_image.convert("RGB")

    def _memory_episode_members(self, bank: Any, entry: Any) -> list[Any]:
        episode_id = str(getattr(entry, "episode_id", "") or "")
        label = _upper_action(getattr(entry, "label", "HOLD"))
        episode_key = episode_id or f"{label}:ungrouped"
        grouped = getattr(bank, "_episode_members", {})
        if isinstance(grouped, Mapping):
            grouped_dict: dict[str, Any] = dict(grouped)
            members = list(cast(Sequence[Any], grouped_dict.get(episode_key, [])))
            if members:
                return members
        fallback = [row for row in list(getattr(bank, "entries", [])) if str(getattr(row, "episode_id", "") or "") == episode_id]
        fallback.sort(key=lambda row: int(getattr(row, "sequence_index", 0) or 0))
        return fallback

    def _memory_wick_signature(self, chart_state: Mapping[str, Any]) -> str:
        candle = _mapping_to_dict(chart_state.get("entry_candle", {}))
        upper = _clip01(candle.get("upper_wick_pct", 0.0))
        lower = _clip01(candle.get("lower_wick_pct", 0.0))
        body = _clip01(candle.get("body_pct", 0.0))
        if upper >= lower + 0.08:
            wick_bias = "upper rejection"
        elif lower >= upper + 0.08:
            wick_bias = "lower absorption"
        else:
            wick_bias = "balanced wick"
        return f"{wick_bias} | body {body:.2f} | upper {upper:.2f} | lower {lower:.2f}"

    def _memory_sequence_story(self, bank: Any, entry: Any) -> str:
        members = self._memory_episode_members(bank, entry)
        if not members:
            return f"{_friendly_phrase(getattr(entry, 'local_phase', ''), fallback='phase')} -> {_friendly_phrase(getattr(entry, 'intent_next', ''), fallback='continue')}"
        sequence_index = int(getattr(entry, "sequence_index", 0) or 0)
        anchor = 0
        for index, member in enumerate(members):
            if str(getattr(member, "entry_id", "")) == str(getattr(entry, "entry_id", "")):
                anchor = index
                break
            if int(getattr(member, "sequence_index", 0) or 0) == sequence_index:
                anchor = index
        window = members[max(0, anchor - 1): min(len(members), anchor + 3)]
        parts = [
            f"{_friendly_phrase(getattr(member, 'local_phase', ''), fallback='phase')}:{_friendly_phrase(getattr(member, 'intent_next', ''), fallback='continue')}"
            for member in window
        ]
        return " -> ".join(parts) if parts else f"{_friendly_phrase(getattr(entry, 'local_phase', ''), fallback='phase')} -> {_friendly_phrase(getattr(entry, 'intent_next', ''), fallback='continue')}"

    def _memory_candle_template(self, entry: Any) -> str:
        chart_state = _mapping_to_dict(getattr(entry, "chart_state", {}))
        continuation_signal = _friendly_phrase(chart_state.get("continuation_signal", ""), fallback="")
        reversal_signal = _friendly_phrase(chart_state.get("reversal_signal", ""), fallback="")
        structure_setup = _friendly_phrase(chart_state.get("structure_setup", ""), fallback="")
        trigger = continuation_signal or reversal_signal or structure_setup or _friendly_phrase(getattr(entry, "intent_next", ""), fallback="continue")
        return (
            f"{_upper_action(getattr(entry, 'label', 'HOLD'))} "
            f"{trigger} | {_friendly_phrase(getattr(entry, 'local_phase', ''), fallback='phase')} | "
            f"{self._memory_wick_signature(chart_state)}"
        ).strip()

    def _score_memory_side_matches(
        self,
        bank: Any,
        query_embed: NDArray[np.float32],
        *,
        desired_label: str,
        macro_trend: str,
        local_phase: str,
        chart_state: Mapping[str, Any],
        query_context: Mapping[str, Any],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        desired = _upper_action(desired_label, fallback="HOLD")
        if desired not in {"BUY", "SELL"}:
            return []
        query = self._normalized_vector(query_embed)
        if query.size == 0:
            return []

        query_tokens = cast(Sequence[Sequence[float]], query_context.get("late_interaction_tokens", []))
        query_trajectory = cast(Sequence[float], query_context.get("trajectory_signature", []))
        query_style = cast(Mapping[str, float], query_context.get("style_signature", {}))
        query_metric = cast(Mapping[str, float], query_context.get("metric_profile", {}))
        query_entry_candle = _mapping_to_dict(chart_state.get("entry_candle", {}))
        continuation_weight = _clip01(chart_state.get("continuation_probability", 0.0))
        pullback_weight = _clip01(chart_state.get("pullback_probability", 0.0))
        reversal_weight = _clip01(chart_state.get("reversal_probability", 0.0))
        fakeout_weight = _clip01(chart_state.get("fakeout_probability", 0.0))
        intent_weights = {
            "continue": continuation_weight,
            "pullback": pullback_weight,
            "reversal_attempt": reversal_weight,
            "fakeout": fakeout_weight,
        }

        retrieval_scores: dict[str, float] = {}
        search_fn = getattr(bank, "search", None)
        if callable(search_fn):
            try:
                search_results = search_fn(
                    query,
                    top_k=max(16, int(limit) * 4),
                    macro_trend=macro_trend or None,
                    local_phase=local_phase or None,
                    query_context=query_context,
                )
            except TypeError:
                try:
                    search_results = search_fn(query, top_k=max(16, int(limit) * 4))
                except Exception as exc:
                    LOGGER.debug("PhoenixGuard memory search fallback failed for %s: %s", desired, exc)
                    search_results = []
            except Exception as exc:
                LOGGER.debug("PhoenixGuard memory search failed for %s: %s", desired, exc)
                search_results = []
            for result in cast(Sequence[Any], search_results or []):
                entry_id = str(getattr(result, "entry_id", "") or "").strip()
                if not entry_id:
                    continue
                result_label = _upper_action(getattr(result, "label", desired), fallback=desired)
                if result_label not in {"", desired}:
                    continue
                result_similarity = _clip01(getattr(result, "similarity", 0.0))
                retrieval_scores[entry_id] = max(retrieval_scores.get(entry_id, 0.0), result_similarity)

        rows: list[dict[str, Any]] = []
        for entry in list(getattr(bank, "entries", [])):
            label = _upper_action(getattr(entry, "label", "HOLD"))
            if label != desired:
                continue
            vector = self._normalized_vector(getattr(entry, "combined_embed", []))
            if vector.size == 0:
                continue
            dim = min(int(query.size), int(vector.size))
            if dim <= 0:
                continue
            vector_similarity = float(max(0.0, float(np.dot(query[:dim], vector[:dim]))))
            entry_id = str(getattr(entry, "entry_id", "") or "").strip()
            retrieval_similarity = retrieval_scores.get(entry_id, 0.0)
            similarity = max(vector_similarity, retrieval_similarity)
            score = similarity
            entry_macro = str(getattr(entry, "macro_trend", "") or "")
            entry_phase = str(getattr(entry, "local_phase", "") or "")
            if macro_trend and entry_macro == macro_trend:
                score += 0.08
            elif macro_trend:
                score -= 0.05
            if local_phase and entry_phase == local_phase:
                score += 0.12
            elif local_phase:
                score -= 0.05
            score += 0.04
            entry_chart_state = _mapping_to_dict(getattr(entry, "chart_state", {}))
            entry_style = cast(Mapping[str, float], getattr(entry, "style_signature", {}))
            if not entry_style:
                entry_style = infer_style_signature_from_chart_state(entry_chart_state)
            entry_metric = cast(Mapping[str, float], getattr(entry, "metric_profile", {}))
            if not entry_metric:
                entry_metric = build_metric_profile(entry_chart_state)
            entry_trajectory = cast(Sequence[float], getattr(entry, "trajectory_signature", []))
            if not entry_trajectory:
                entry_trajectory = build_trajectory_signature(
                    entry_chart_state,
                    sequence_index=int(getattr(entry, "sequence_index", 0) or 0),
                )
            entry_late_tokens = cast(Sequence[Sequence[float]], getattr(entry, "late_interaction_tokens", []))
            if not entry_late_tokens:
                entry_late_tokens = build_late_interaction_tokens(
                    entry_chart_state,
                    combined_embed=list(cast(Sequence[float], getattr(entry, "combined_embed", []))),
                    style_signature=entry_style,
                    metric_profile=entry_metric,
                )
            late_score = late_interaction_score(query_tokens, entry_late_tokens)
            trajectory_score = trajectory_alignment(query_trajectory, entry_trajectory)
            style_score = style_alignment_score(query_style, entry_style)
            metric_score = metric_profile_alignment(query_metric, entry_metric)
            score += 0.12 * late_score + 0.08 * trajectory_score + 0.05 * style_score + 0.11 * metric_score
            entry_candle = _mapping_to_dict(entry_chart_state.get("entry_candle", {}))
            wick_alignment = 1.0 - float(
                np.mean(
                    np.asarray(
                        [
                            abs(_clip01(query_entry_candle.get("upper_wick_pct", 0.0)) - _clip01(entry_candle.get("upper_wick_pct", 0.0))),
                            abs(_clip01(query_entry_candle.get("lower_wick_pct", 0.0)) - _clip01(entry_candle.get("lower_wick_pct", 0.0))),
                            abs(_clip01(query_entry_candle.get("body_pct", 0.0)) - _clip01(entry_candle.get("body_pct", 0.0))),
                        ],
                        dtype=np.float32,
                    )
                )
            )
            wick_alignment = _clip01(wick_alignment)
            score += 0.06 * wick_alignment
            intent_key = str(getattr(entry, "intent_next", "continue") or "continue")
            intent_alignment = _clip01(intent_weights.get(intent_key, continuation_weight))
            score += 0.08 * intent_alignment
            macro_score = 1.0 if macro_trend and entry_macro == macro_trend else (0.55 if not macro_trend or not entry_macro else 0.18)
            phase_score = 1.0 if local_phase and entry_phase == local_phase else (0.55 if not local_phase or not entry_phase else 0.18)
            entry_teaching = _mapping_to_dict(entry_chart_state.get("memory_teaching", {}))
            entry_progression = _mapping_to_dict(entry_chart_state.get("entry_progression", {}))
            entry_sniper = _mapping_to_dict(entry_chart_state.get("sniper_profile", {}))
            query_regression = _mapping_to_dict(chart_state.get("memory_candle_regression", {}))
            entry_regression = _mapping_to_dict(entry_chart_state.get("memory_candle_regression", entry_progression.get("candle_regression", {})))
            query_regression_direction = _upper_action(query_regression.get("direction"), fallback="HOLD")
            entry_regression_direction = _upper_action(entry_regression.get("direction"), fallback="HOLD")
            regression_direction_score = (
                1.0
                if query_regression_direction == entry_regression_direction and query_regression_direction in {"BUY", "SELL"}
                else 0.58
                if "HOLD" in {query_regression_direction, entry_regression_direction}
                else 0.20
            )
            regression_score = _clip01(
                0.44 * regression_direction_score
                + 0.26
                * (
                    1.0
                    - abs(
                        _clip01(query_regression.get("confidence", 0.0))
                        - _clip01(entry_regression.get("confidence", entry_progression.get("regression_confidence", 0.0)))
                    )
                )
                + 0.30 * _clip01(entry_regression.get("alignment_to_label", 0.0))
            )
            lesson_role = str(entry_teaching.get("lesson_role", entry_progression.get("progression_stage", "")) or "").lower()
            lesson_score = _clip01(
                max(
                    _clip01(entry_teaching.get("teaching_weight", 0.0)),
                    _clip01(entry_teaching.get("actual_entry_score", 0.0)),
                    _clip01(entry_teaching.get("win_evidence_score", 0.0)),
                    _clip01(entry_teaching.get("progression_score", 0.0)),
                )
                + (0.05 if lesson_role in {"actual_entry", "win_resolution"} else 0.0)
            )
            aggressive_score = _clip01(
                entry_sniper.get("aggressive_entry_score", entry_progression.get("aggressive_sniper_score", 0.0))
            )
            score += 0.08 * lesson_score + 0.07 * aggressive_score + 0.05 * regression_score
            precision_score = _clip01(
                0.28 * similarity
                + 0.12 * late_score
                + 0.09 * trajectory_score
                + 0.07 * style_score
                + 0.11 * metric_score
                + 0.07 * wick_alignment
                + 0.035 * intent_alignment
                + 0.03 * macro_score
                + 0.03 * phase_score
                + 0.07 * lesson_score
                + 0.055 * aggressive_score
                + 0.04 * regression_score
            )
            rows.append(
                {
                    "entry": entry,
                    "similarity": similarity,
                    "embedding_similarity": vector_similarity,
                    "retrieval_similarity": retrieval_similarity,
                    "score": score,
                    "precision_score": precision_score,
                    "late_score": late_score,
                    "trajectory_score": trajectory_score,
                    "style_score": style_score,
                    "metric_score": metric_score,
                    "wick_alignment": wick_alignment,
                    "intent_weight": intent_alignment,
                    "macro_score": macro_score,
                    "phase_score": phase_score,
                    "lesson_score": lesson_score,
                    "aggressive_score": aggressive_score,
                    "regression_score": regression_score,
                }
            )

        rows.sort(
            key=lambda row: (float(row["precision_score"]), float(row["score"]), float(row["similarity"])),
            reverse=True,
        )
        return rows[: max(1, int(limit))]

    def _memory_match_payload(self, bank: Any, row: Mapping[str, Any]) -> dict[str, Any]:
        entry = row.get("entry")
        if entry is None:
            return {}
        chart_state = _mapping_to_dict(getattr(entry, "chart_state", {}))
        teaching = _mapping_to_dict(chart_state.get("memory_teaching", {}))
        progression = _mapping_to_dict(chart_state.get("entry_progression", {}))
        sniper_profile = _mapping_to_dict(chart_state.get("sniper_profile", {}))
        candle_regression = _mapping_to_dict(chart_state.get("memory_candle_regression", progression.get("candle_regression", {})))
        episode_members = self._memory_episode_members(bank, entry)
        resolved_image_path = _resolve_memory_source_path(getattr(entry, "image_path", ""))
        raw_image_path = str(getattr(entry, "image_path", "") or "")
        image_path = str(resolved_image_path) if str(resolved_image_path).strip() else raw_image_path
        image_name = Path(image_path or raw_image_path or str(getattr(entry, "entry_id", "match"))).name
        return {
            "entry_id": str(getattr(entry, "entry_id", "")),
            "label": _upper_action(getattr(entry, "label", "HOLD")),
            "similarity": round(float(row.get("similarity", 0.0) or 0.0), 4),
            "embedding_similarity": round(float(row.get("embedding_similarity", 0.0) or 0.0), 4),
            "retrieval_similarity": round(float(row.get("retrieval_similarity", 0.0) or 0.0), 4),
            "score": round(float(row.get("score", 0.0) or 0.0), 4),
            "precision_score": round(float(row.get("precision_score", 0.0) or 0.0), 4),
            "alignment": {
                "retrieval": round(float(row.get("retrieval_similarity", 0.0) or 0.0), 4),
                "embedding": round(float(row.get("embedding_similarity", 0.0) or 0.0), 4),
                "late": round(float(row.get("late_score", 0.0) or 0.0), 4),
                "trajectory": round(float(row.get("trajectory_score", 0.0) or 0.0), 4),
                "style": round(float(row.get("style_score", 0.0) or 0.0), 4),
                "metrics": round(float(row.get("metric_score", 0.0) or 0.0), 4),
                "wick": round(float(row.get("wick_alignment", 0.0) or 0.0), 4),
                "intent": round(float(row.get("intent_weight", 0.0) or 0.0), 4),
                "macro": round(float(row.get("macro_score", 0.0) or 0.0), 4),
                "phase": round(float(row.get("phase_score", 0.0) or 0.0), 4),
                "lesson": round(float(row.get("lesson_score", 0.0) or 0.0), 4),
                "aggressive": round(float(row.get("aggressive_score", 0.0) or 0.0), 4),
                "regression": round(float(row.get("regression_score", 0.0) or 0.0), 4),
            },
            "memory_teaching": teaching,
            "entry_progression": progression,
            "sniper_profile": sniper_profile,
            "candle_regression": candle_regression,
            "lesson_role": str(teaching.get("lesson_role", progression.get("progression_stage", "")) or ""),
            "aggressive_entry_score": round(float(row.get("aggressive_score", 0.0) or 0.0), 4),
            "image_name": image_name,
            "image_path": image_path,
            "image_exists": bool(str(resolved_image_path).strip()) and resolved_image_path.exists(),
            "macro_trend": str(getattr(entry, "macro_trend", "") or ""),
            "local_phase": str(getattr(entry, "local_phase", "") or ""),
            "intent_next": str(getattr(entry, "intent_next", "") or ""),
            "phase_risk": str(getattr(entry, "phase_risk", "") or ""),
            "episode_id": str(getattr(entry, "episode_id", "") or ""),
            "sequence_index": int(getattr(entry, "sequence_index", 0) or 0),
            "episode_length": int(len(episode_members)),
            "wick_signature": self._memory_wick_signature(chart_state),
            "sequence_story": self._memory_sequence_story(bank, entry),
            "pre_entry_sequence": [
                str(token)
                for token in cast(Sequence[Any], chart_state.get("pre_entry_sequence", []))[:4]
                if str(token).strip()
            ],
            "structure_setup": _friendly_phrase(chart_state.get("structure_setup", ""), fallback=""),
            "match_quality": (
                "tight"
                if float(row.get("precision_score", 0.0) or 0.0) >= _MEMORY_PRECISION_STRONG_SCORE
                else "usable"
                if float(row.get("precision_score", 0.0) or 0.0) >= _MEMORY_PRECISION_MIN_SCORE
                else "loose"
            ),
        }

    def _memory_transition_bias_from_rows(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        weights = {"continue": 0.0, "pullback": 0.0, "reversal_attempt": 0.0, "fakeout": 0.0}
        total = 0.0
        for row in rows:
            entry = row.get("entry")
            if entry is None:
                continue
            key = str(getattr(entry, "intent_next", "continue") or "continue")
            if key not in weights:
                key = "continue"
            weight = max(0.0, float(row.get("similarity", 0.0) or 0.0))
            weights[key] += weight
            total += weight
        if total <= 1e-9:
            return {key: 0.0 for key in weights}
        return {key: round(float(value / total), 4) for key, value in weights.items()}

    def _memory_fit_payload(
        self,
        bank: Any,
        rows: Sequence[Mapping[str, Any]],
        *,
        side: str,
    ) -> dict[str, Any]:
        desired = _upper_action(side, fallback="HOLD")
        if desired not in {"BUY", "SELL"} or not rows:
            note = f"No clear {desired.lower()} memory fit was available."
            return {
                "summary": note,
                "top_matches": [],
                "top_predictions": [],
                "transition_bias": {},
                "candle_templates": [],
            }
        top_matches = [self._memory_match_payload(bank, row) for row in rows[:3]]
        top_matches = [row for row in top_matches if row]
        top_predictions = [
            {
                "rank": index + 1,
                "side": _upper_action(match.get("label", desired), fallback=desired),
                "image_name": str(match.get("image_name", "memory") or "memory"),
                "similarity": round(float(match.get("similarity", 0.0) or 0.0), 4),
                "precision": round(float(match.get("precision_score", 0.0) or 0.0), 4),
                "lesson_role": str(match.get("lesson_role", "") or ""),
                "aggressive_entry_score": round(float(match.get("aggressive_entry_score", 0.0) or 0.0), 4),
                "regression": _mapping_to_dict(match.get("candle_regression", {})),
                "expected_path": _friendly_phrase(match.get("intent_next", ""), fallback="memory follow through"),
            }
            for index, match in enumerate(top_matches[:3])
        ]
        top_entry = _mapping_to_dict(top_matches[0]) if top_matches else {}
        transition_bias = self._memory_transition_bias_from_rows(rows[:5])
        candle_templates = [
            self._memory_candle_template(row.get("entry"))
            for row in rows[:3]
            if row.get("entry") is not None
        ]
        average_precision = float(
            np.mean(np.asarray([float(row.get("precision_score", 0.0) or 0.0) for row in rows[:3]], dtype=np.float32))
        )
        high_precision_count = sum(
            1 for row in rows[:5] if float(row.get("precision_score", 0.0) or 0.0) >= _MEMORY_PRECISION_MIN_SCORE
        )
        bias_text = ""
        if transition_bias:
            next_event, probability = max(transition_bias.items(), key=lambda item: item[1])
            bias_text = f"{_friendly_phrase(next_event)} {probability:.0%}"
        summary = (
            f"{desired} fit led by {str(top_entry.get('image_name', 'memory'))} "
            f"similarity {float(top_entry.get('similarity', 0.0) or 0.0):.2f} "
            f"precision {float(top_entry.get('precision_score', 0.0) or 0.0):.2f} | "
            f"{_friendly_phrase(top_entry.get('sequence_story', ''), fallback='sequence match')} | "
            f"{bias_text or 'transition bias pending'}."
        ).strip()
        return {
            "summary": summary,
            "top_matches": top_matches,
            "top_predictions": top_predictions,
            "transition_bias": transition_bias,
            "candle_templates": candle_templates,
            "average_precision": round(average_precision, 4),
            "high_precision_count": int(high_precision_count),
        }

    @staticmethod
    def _memory_counter_behavior_payload(counter_fit: Mapping[str, Any]) -> dict[str, Any]:
        counter_matches = [
            _mapping_to_dict(item)
            for item in cast(Sequence[Any], counter_fit.get("top_matches", []))
            if isinstance(item, Mapping)
        ]
        counter_top = counter_matches[0] if counter_matches else {}
        transition_bias = _mapping_to_dict(counter_fit.get("transition_bias", {}))
        continue_bias = _clip01(transition_bias.get("continue", 0.0))
        reversal_bias = _clip01(transition_bias.get("reversal_attempt", 0.0))
        pullback_bias = _clip01(transition_bias.get("pullback", 0.0))
        fakeout_bias = _clip01(transition_bias.get("fakeout", 0.0))
        continuation_risk = _clip01(continue_bias + 0.72 * reversal_bias)
        failure_or_probe = _clip01(pullback_bias + fakeout_bias)
        counter_precision = _clip01(counter_top.get("precision_score", 0.0))
        counter_similarity = _clip01(counter_top.get("similarity", 0.0))
        hard_counter_risk = bool(counter_precision >= 0.72 and continuation_risk >= 0.58 and failure_or_probe < 0.36)
        supports_primary = bool(failure_or_probe >= 0.38 or (continuation_risk <= 0.45 and counter_precision <= 0.72))
        if hard_counter_risk:
            state = "counter_continuation_risk"
            message = "Counter memories tend to continue; do not override this as a clean aggressive entry."
        elif supports_primary:
            state = "counter_probe_or_failure"
            message = "Counter memories mostly behave like probe, pullback, or fakeout before the primary side resolves."
        else:
            state = "counter_mixed"
            message = "Counter memories are close enough to demand tighter trigger alignment."
        return {
            "state": state,
            "supports_primary": supports_primary,
            "hard_counter_risk": hard_counter_risk,
            "continuation_risk": round(float(continuation_risk), 4),
            "failure_or_probe": round(float(failure_or_probe), 4),
            "counter_precision": round(float(counter_precision), 4),
            "counter_similarity": round(float(counter_similarity), 4),
            "message": message,
        }

    @staticmethod
    def _memory_precision_payload(
        primary_fit: Mapping[str, Any],
        counter_fit: Mapping[str, Any],
    ) -> dict[str, Any]:
        primary_matches = cast(Sequence[Any], primary_fit.get("top_matches", []))
        counter_matches = cast(Sequence[Any], counter_fit.get("top_matches", []))
        primary_top = _mapping_to_dict(primary_matches[0]) if primary_matches else {}
        counter_top = _mapping_to_dict(counter_matches[0]) if counter_matches else {}
        primary_rows = [_mapping_to_dict(item) for item in primary_matches if isinstance(item, Mapping)]
        primary_similarity = _clip01(primary_top.get("similarity", 0.0))
        primary_precision = _clip01(primary_top.get("precision_score", 0.0))
        counter_similarity = _clip01(counter_top.get("similarity", 0.0))
        counter_precision = _clip01(counter_top.get("precision_score", 0.0))
        edge = round(primary_similarity - counter_similarity, 4) if counter_top else round(primary_similarity, 4)
        precision_edge = round(primary_precision - counter_precision, 4) if counter_top else round(primary_precision, 4)
        primary_stack_precision = (
            float(np.mean(np.asarray([_clip01(row.get("precision_score", 0.0)) for row in primary_rows[:3]], dtype=np.float32)))
            if primary_rows
            else 0.0
        )
        primary_high_count = int(primary_fit.get("high_precision_count", 0) or 0)
        if primary_high_count <= 0:
            primary_high_count = sum(1 for row in primary_rows[:5] if _clip01(row.get("precision_score", 0.0)) >= _MEMORY_PRECISION_MIN_SCORE)
        counter_behavior = PhoenixGuardWindowTrackingAdapter._memory_counter_behavior_payload(counter_fit)
        strict_edge_accept = (
            primary_similarity >= _MEMORY_PRECISION_MIN_SIMILARITY
            and primary_precision >= _MEMORY_PRECISION_MIN_SCORE
            and edge >= _MEMORY_PRECISION_MIN_EDGE
        )
        stacked_favor_accept = (
            primary_similarity >= max(_MEMORY_PRECISION_MIN_SIMILARITY, 0.78)
            and primary_precision >= _MEMORY_PRECISION_MIN_SCORE
            and primary_stack_precision >= 0.68
            and primary_high_count >= 1
            and precision_edge >= -0.06
            and (edge >= -0.03 or (edge >= -0.06 and precision_edge >= 0.08))
            and not bool(counter_behavior.get("hard_counter_risk", False))
        )
        accepted = strict_edge_accept or stacked_favor_accept
        accepted_by = "strict_edge" if strict_edge_accept else "stacked_favor" if stacked_favor_accept else "rejected"
        quality = (
            "high_precision"
            if accepted and primary_precision >= _MEMORY_PRECISION_STRONG_SCORE and strict_edge_accept
            else "aggressive_stacked"
            if accepted and stacked_favor_accept
            else "usable"
            if accepted
            else "rejected"
        )
        reason = (
            f"accepted: similarity {primary_similarity:.2f}, precision {primary_precision:.2f}, edge {edge:+.2f}"
            if strict_edge_accept
            else (
                f"aggressive stacked accept: primary precision {primary_precision:.2f}, stack {primary_stack_precision:.2f}, "
                f"edge {edge:+.2f}; {str(counter_behavior.get('message', 'counter checked'))}"
            )
            if accepted
            else (
                f"rejected: needs similarity>={_MEMORY_PRECISION_MIN_SIMILARITY:.2f}, "
                f"precision>={_MEMORY_PRECISION_MIN_SCORE:.2f}, edge>={_MEMORY_PRECISION_MIN_EDGE:.2f}; "
                f"got {primary_similarity:.2f}, {primary_precision:.2f}, {edge:+.2f}; "
                f"{str(counter_behavior.get('message', 'counter checked'))}"
            )
        )
        return {
            "accepted": bool(accepted),
            "quality": quality,
            "accepted_by": accepted_by,
            "primary_similarity": round(primary_similarity, 4),
            "primary_precision": round(primary_precision, 4),
            "primary_stack_precision": round(float(primary_stack_precision), 4),
            "primary_high_precision_count": int(primary_high_count),
            "counter_similarity": round(counter_similarity, 4),
            "counter_precision": round(counter_precision, 4),
            "counter_behavior": counter_behavior,
            "edge": edge,
            "precision_edge": precision_edge,
            "minimum_similarity": _MEMORY_PRECISION_MIN_SIMILARITY,
            "minimum_precision": _MEMORY_PRECISION_MIN_SCORE,
            "minimum_edge": _MEMORY_PRECISION_MIN_EDGE,
            "reason": reason,
        }

    @staticmethod
    def _tighten_memory_bbox(
        bbox: Sequence[Any],
        *,
        precision_score: float,
        role: str,
    ) -> list[int]:
        if len(bbox) < 4:
            return []
        x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
        if x1 <= x0 or y1 <= y0:
            return [int(round(x0)), int(round(y0)), int(round(max(x0 + 1.0, x1))), int(round(max(y0 + 1.0, y1)))]
        precision = max(_MEMORY_PRECISION_TIGHTEN_MIN, _clip01(precision_score))
        role_key = str(role or "").strip().lower()
        if role_key in {"aggressive_entry", "sniper", "watch"}:
            base_scale = 0.60
            min_scale = 0.46
        elif role_key == "trigger":
            base_scale = 0.66
            min_scale = 0.50
        elif role_key == "target":
            base_scale = 0.76
            min_scale = 0.58
        else:
            base_scale = 0.86
            min_scale = 0.62
        scale = max(min_scale, min(0.90, base_scale - (precision - _MEMORY_PRECISION_TIGHTEN_MIN) * 0.22))
        width = x1 - x0
        height = y1 - y0
        min_width = max(12.0, min(width, 18.0))
        min_height = max(8.0, min(height, 12.0))
        next_width = max(min_width, width * scale)
        next_height = max(min_height, height * scale)
        cx = (x0 + x1) * 0.5
        cy = (y0 + y1) * 0.5
        return [
            int(round(cx - next_width * 0.5)),
            int(round(cy - next_height * 0.5)),
            int(round(cx + next_width * 0.5)),
            int(round(cy + next_height * 0.5)),
        ]

    def _memory_projection_hotspots(
        self,
        tracking_summary: Mapping[str, Any],
        *,
        dominant_side: str,
        primary_fit: Mapping[str, Any],
        counter_fit: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        projection: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("projection") or {})
        current_box: dict[str, Any] = cast(dict[str, Any], tracking_summary.get("current_box") or {})
        zones = _sequence_of_mappings(projection.get("zones", []))
        sniper_zone: dict[str, Any] = {}
        primary_zone: dict[str, Any] = {}
        for zone in zones:
            kind = str(zone.get("kind", "")).lower()
            if kind == "sniper" and not sniper_zone:
                sniper_zone = zone
            elif kind == "primary" and not primary_zone:
                primary_zone = zone
        primary_top = _first_mapping(primary_fit.get("top_matches", []))
        counter_top = _first_mapping(counter_fit.get("top_matches", []))
        primary_matches = _sequence_of_mappings(primary_fit.get("top_matches", []))
        primary_precision = _clip01(primary_top.get("precision_score", primary_top.get("similarity", 0.0)))
        counter_precision = _clip01(counter_top.get("precision_score", counter_top.get("similarity", 0.0)))
        chart_region = _mapping_to_dict(tracking_summary.get("chart_region", tracking_summary.get("display_region", {})))
        chart_width = int(chart_region.get("width", 0) or 0)
        chart_height = int(chart_region.get("height", 0) or 0)
        if chart_width <= 0 or chart_height <= 0:
            candidates: list[Sequence[Any]] = [
                cast(Sequence[Any], sniper_zone.get("bbox", [])),
                cast(Sequence[Any], primary_zone.get("bbox", [])),
                cast(Sequence[Any], primary_zone.get("target_bbox", [])),
                cast(Sequence[Any], current_box.get("bbox", [])),
            ]
            chart_width = int(max([float(box[2]) for box in candidates if len(box) >= 4] or [0.0]))
            chart_height = int(max([float(box[3]) for box in candidates if len(box) >= 4] or [0.0]))

        def normalized_bbox(raw_bbox: Sequence[Any]) -> list[int]:
            if len(raw_bbox) < 4 or chart_width <= 0 or chart_height <= 0:
                return []
            values = [float(value) for value in raw_bbox[:4]]
            if max(abs(value) for value in values) > 1.0001:
                return [int(round(value)) for value in values]
            return [
                int(round(values[0] * chart_width)),
                int(round(values[1] * chart_height)),
                int(round(values[2] * chart_width)),
                int(round(values[3] * chart_height)),
            ]

        hotspots: list[dict[str, Any]] = []
        if cast(Sequence[Any], sniper_zone.get("bbox", [])):
            sniper_profile = _mapping_to_dict(primary_top.get("sniper_profile", {}))
            watch_bbox = normalized_bbox(cast(Sequence[Any], sniper_profile.get("watch_window_norm", [])))
            if not watch_bbox:
                watch_bbox = cast(list[int], list(cast(Sequence[Any], sniper_zone.get("bbox", []))))
            hotspots.append(
                {
                    "role": "aggressive_entry",
                    "label": (
                        f"{dominant_side} AGGRO SNIPER | {str(primary_top.get('image_name', 'memory fit'))} "
                        f"{float(primary_top.get('aggressive_entry_score', 0.0) or 0.0):.2f}"
                    ).strip(),
                    "direction": dominant_side,
                    "confidence": primary_precision,
                    "bbox": self._tighten_memory_bbox(
                        watch_bbox,
                        precision_score=primary_precision,
                        role="aggressive_entry",
                    ),
                    "rank": 1,
                }
            )
        if cast(Sequence[Any], primary_zone.get("bbox", [])):
            hotspots.append(
                {
                    "role": "trigger",
                    "label": (
                        f"{dominant_side} trigger | {_friendly_phrase(primary_top.get('intent_next', ''), fallback='memory release')} | "
                        f"{str(primary_top.get('image_name', 'memory fit'))}"
                    ).strip(),
                    "direction": dominant_side,
                    "confidence": primary_precision,
                    "bbox": self._tighten_memory_bbox(
                        cast(Sequence[Any], primary_zone.get("bbox", [])),
                        precision_score=primary_precision,
                        role="trigger",
                    ),
                    "rank": 1,
                }
            )
            primary_bbox = [float(value) for value in cast(Sequence[Any], primary_zone.get("bbox", []))[:4]]
            if len(primary_bbox) >= 4:
                height = max(4.0, primary_bbox[3] - primary_bbox[1])
                shift_sign = 1.0 if dominant_side == "SELL" else -1.0
                for rank, match in enumerate(primary_matches[1:3], start=2):
                    rank_precision = _clip01(match.get("precision_score", match.get("similarity", 0.0)))
                    y_shift = shift_sign * height * (0.72 + (rank - 2) * 0.62)
                    x_shift = float(rank - 1) * 4.0
                    alt_bbox = [
                        primary_bbox[0] + x_shift,
                        primary_bbox[1] + y_shift,
                        primary_bbox[2] + x_shift,
                        primary_bbox[3] + y_shift,
                    ]
                    hotspots.append(
                        {
                            "role": f"forecast_{rank}",
                            "label": (
                                f"TOP {rank} | {dominant_side} | {str(match.get('image_name', 'memory'))} "
                                f"{rank_precision:.2f}"
                            ).strip(),
                            "direction": dominant_side,
                            "confidence": rank_precision,
                            "bbox": self._tighten_memory_bbox(
                                alt_bbox,
                                precision_score=rank_precision,
                                role="trigger",
                            ),
                            "rank": rank,
                        }
                    )
        if cast(Sequence[Any], primary_zone.get("target_bbox", [])):
            hotspots.append(
                {
                    "role": "target",
                    "label": f"{dominant_side} target | {str(primary_top.get('image_name', 'memory path'))}".strip(),
                    "direction": dominant_side,
                    "confidence": primary_precision,
                    "bbox": self._tighten_memory_bbox(
                        cast(Sequence[Any], primary_zone.get("target_bbox", [])),
                        precision_score=primary_precision,
                        role="target",
                    ),
                }
            )
        invalidation_raw = primary_zone.get("invalidation_y", sniper_zone.get("invalidation_y"))
        if invalidation_raw is not None:
            try:
                invalidation_y = int(round(float(invalidation_raw)))
                reference_bbox = cast(Sequence[Any], primary_zone.get("bbox", current_box.get("bbox", [])))
                if len(reference_bbox) >= 4:
                    x0 = int(reference_bbox[0])
                    x1 = int(reference_bbox[2])
                    hotspots.append(
                        {
                            "role": "invalidation",
                            "label": f"{dominant_side} invalidation".strip(),
                            "direction": dominant_side,
                            "confidence": primary_precision,
                            "bbox": [x0, max(0, invalidation_y - 2), x1, invalidation_y + 2],
                        }
                    )
            except (TypeError, ValueError):
                pass
        if cast(Sequence[Any], current_box.get("bbox", [])) and counter_top:
            hotspots.append(
                {
                    "role": "counter",
                    "label": (
                        f"{str(counter_top.get('label', _opposite_action(dominant_side)))} counter check | "
                        f"{str(counter_top.get('image_name', 'counter fit'))} {float(counter_top.get('similarity', 0.0) or 0.0):.2f}"
                    ).strip(),
                    "direction": str(counter_top.get("label", _opposite_action(dominant_side)) or _opposite_action(dominant_side)),
                    "confidence": counter_precision,
                    "bbox": self._tighten_memory_bbox(
                        cast(Sequence[Any], current_box.get("bbox", [])),
                        precision_score=counter_precision,
                        role="counter",
                    ),
                }
            )
        return hotspots

    def _memory_projection_reference_path(self, projection_payload: Mapping[str, Any]) -> Path | None:
        primary_fit = _mapping_to_dict(projection_payload.get("primary_fit", {}))
        top_matches = cast(Sequence[Any], primary_fit.get("top_matches", []))
        top_match = _mapping_to_dict(top_matches[0]) if top_matches else {}
        raw_reference_path = str(top_match.get("image_path", "") or "").strip()
        if not raw_reference_path:
            return None
        reference_path = _resolve_memory_source_path(raw_reference_path)
        if not str(reference_path).strip() or not reference_path.exists():
            return None
        return reference_path

    def _projection_preview_source_zones(
        self,
        tracking_summary: Mapping[str, Any],
        *,
        mode: str,
    ) -> list[dict[str, Any]]:
        projection = _mapping_to_dict(tracking_summary.get("projection", {}))
        zones = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], projection.get("zones", []))
            if isinstance(item, Mapping)
        ]
        normalized_mode = "future" if str(mode or "").strip().lower() == "future" else "predict"
        if normalized_mode == "future":
            return zones
        return [
            zone
            for zone in zones
            if str(zone.get("kind", "") or "").strip().lower() in {"sniper", "primary"}
        ]

    def _render_memory_projection_preview(
        self,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        projection_payload: Mapping[str, Any],
    ) -> Image.Image:
        normalized_mode = "future" if str(projection_payload.get("mode", "")).strip().lower() == "future" else "predict"
        chart_image = self._chart_image_from_tracking_summary(surface_image, tracking_summary).convert("RGB")
        zones = self._projection_preview_source_zones(tracking_summary, mode=normalized_mode)
        if not zones:
            return chart_image

        future_x_values: list[float] = []
        for zone in zones:
            bbox = cast(Sequence[Any], zone.get("bbox", []))
            target_bbox = cast(Sequence[Any], zone.get("target_bbox", []))
            if len(bbox) >= 4:
                future_x_values.extend((float(bbox[0]), float(bbox[2])))
            if normalized_mode == "future" and len(target_bbox) >= 4:
                future_x_values.extend((float(target_bbox[0]), float(target_bbox[2])))
            path = cast(Sequence[Any], zone.get("path", []))
            for point in path[1:]:
                row = cast(Sequence[Any], point)
                if len(row) >= 2:
                    future_x_values.append(float(row[0]))
        if not future_x_values:
            return chart_image

        chart_width = max(1, int(chart_image.width))
        chart_height = max(1, int(chart_image.height))
        runway_gap = max(28, int(round(chart_width * 0.05)))
        zone_min_x = min(future_x_values)
        zone_max_x = max(future_x_values)
        shift_x = float(chart_width + runway_gap) - float(zone_min_x)
        preview_width = int(max(chart_width + 180, zone_max_x + shift_x + runway_gap))
        preview_bounds = [0, 0, preview_width, chart_height]

        preview = Image.new("RGBA", (preview_width, chart_height), (8, 12, 18, 255))
        preview.paste(chart_image.convert("RGBA"), (0, 0))

        draw = ImageDraw.Draw(preview, "RGBA")
        runway_width = max(1, preview_width - chart_width)
        right_strip_width = max(8, min(28, chart_width))
        right_strip = chart_image.crop((max(0, chart_width - right_strip_width), 0, chart_width, chart_height))
        if runway_width > 0:
            preview.paste(right_strip.resize((runway_width, chart_height)), (chart_width, 0))
            draw.rectangle((chart_width, 0, preview_width, chart_height), fill=(6, 12, 18, 196))

        vertical_step = max(30, int(round(chart_width * 0.085)))
        horizontal_step = max(28, int(round(chart_height * 0.12)))
        for x in range(chart_width + max(10, runway_gap // 2), preview_width, vertical_step):
            draw.line((x, 0, x, chart_height), fill=(255, 255, 255, 18), width=1)
        for y in range(horizontal_step // 2, chart_height, horizontal_step):
            draw.line((chart_width, y, preview_width, y), fill=(255, 255, 255, 14), width=1)
        draw.line((chart_width, 0, chart_width, chart_height), fill=(242, 200, 102, 86), width=2)

        label_font = _overlay_font(max(11, int(round(preview_width * 0.0105))), bold=True)
        caption_font = _overlay_font(max(10, int(round(preview_width * 0.009))), bold=False)
        dominant_side = _upper_action(projection_payload.get("dominant_side", latest_signal.get("action", "HOLD")))
        top_matches = _sequence_of_mappings(_mapping_to_dict(projection_payload.get("primary_fit", {})).get("top_matches", []))
        top_match = top_matches[0] if top_matches else {}
        reference_name = str(
            projection_payload.get("reference_image_name", top_match.get("image_name", "memory fit")) or "memory fit"
        )
        mode_label = "SHOW FUTURE" if normalized_mode == "future" else "PREDICT"
        header_label = f"{mode_label} | {dominant_side} | memory {reference_name} {float(projection_payload.get('memory_similarity', 0.0) or 0.0):.2f}"
        header_width, _header_height = self._overlay_tag_size(draw, header_label, font=caption_font, padding_x=10, padding_y=4)
        header_x = max(chart_width + 12, preview_width - header_width - 12)
        projection_precision = _clip01(
            projection_payload.get("memory_precision_score", projection_payload.get("memory_similarity", 0.0))
        )
        self._draw_overlay_tag(
            draw,
            (header_x, 12),
            header_label,
            font=caption_font,
            fill=(7, 16, 22, 224),
            outline=(242, 200, 102, 156),
            text_fill=(242, 200, 102, 236),
            padding_x=10,
            padding_y=4,
        )
        prediction_stack = _sequence_of_mappings(projection_payload.get("prediction_stack", []))[:3]
        if prediction_stack:
            tag_items: list[dict[str, Any]] = []
            for row in prediction_stack:
                rank = int(row.get("rank", len(tag_items) + 1) or (len(tag_items) + 1))
                tag_items.append(
                    {
                        "label": (
                            f"TOP {rank} {str(row.get('image_name', 'memory'))} "
                            f"P{float(row.get('precision', 0.0) or 0.0):.2f} "
                            f"A{float(row.get('aggressive_entry_score', 0.0) or 0.0):.2f}"
                        ),
                        "fill": (7, 16, 22, 204),
                        "outline": (242, 200, 102, 124),
                        "text_fill": (242, 200, 102, 226),
                    }
                )
            placements, _ = self._layout_overlay_tags(
                draw,
                tag_items,
                (chart_width + 12, 42),
                max_width=max(120, preview_width - chart_width - 24),
                font=caption_font,
                gap=6,
                row_gap=6,
            )
            self._draw_overlay_tag_group(draw, placements, font=caption_font)

        if chart_width > 10:
            self._draw_overlay_tag(
                draw,
                (12, 12),
                "LIVE INPUT",
                font=caption_font,
                fill=(7, 16, 22, 204),
                outline=(107, 200, 255, 126),
                text_fill=(107, 200, 255, 228),
                padding_x=8,
                padding_y=4,
            )

        for zone in zones:
            kind = str(zone.get("kind", "primary") or "primary").strip().lower()
            direction = _upper_action(zone.get("direction", dominant_side), fallback=dominant_side)
            color: ColorRGB = (
                (96, 218, 145)
                if direction == "BUY"
                else (255, 122, 99)
                if direction == "SELL"
                else (138, 160, 181)
            )
            bbox = cast(Sequence[Any], zone.get("bbox", []))
            if len(bbox) >= 4:
                tight_bbox = self._tighten_memory_bbox(
                    bbox,
                    precision_score=projection_precision,
                    role="trigger" if kind in {"primary", "sniper"} else kind,
                )
                shifted_bbox = _clip_bbox_to_bounds(
                    preview_bounds,
                    [float(tight_bbox[0]) + shift_x, float(tight_bbox[1]), float(tight_bbox[2]) + shift_x, float(tight_bbox[3])],
                )
                self._draw_projection_zone(
                    draw,
                    shifted_bbox,
                    label=str(zone.get("label", f"{direction} TRIGGER") or f"{direction} TRIGGER"),
                    color=color,
                    font=label_font,
                    confidence=_clip01(zone.get("confidence", 0.0)),
                    primary=kind in {"sniper", "primary"},
                    bounds=preview_bounds,
                )

            target_bbox = cast(Sequence[Any], zone.get("target_bbox", []))
            if normalized_mode == "future" and len(target_bbox) >= 4:
                tight_target = self._tighten_memory_bbox(
                    target_bbox,
                    precision_score=projection_precision,
                    role="target",
                )
                shifted_target = _clip_bbox_to_bounds(
                    preview_bounds,
                    [
                        float(tight_target[0]) + shift_x,
                        float(tight_target[1]),
                        float(tight_target[2]) + shift_x,
                        float(tight_target[3]),
                    ],
                )
                self._draw_projection_zone(
                    draw,
                    shifted_target,
                    label=f"{direction} TARGET",
                    color=color,
                    font=label_font,
                    confidence=_clip01(zone.get("confidence", 0.0)),
                    primary=False,
                    bounds=preview_bounds,
                )

            raw_path = cast(Sequence[Any], zone.get("path", []))
            if raw_path:
                limit = len(raw_path) if normalized_mode == "future" else min(len(raw_path), 3)
                shifted_path: list[tuple[int, int]] = []
                for index, point in enumerate(raw_path[:limit]):
                    row = cast(Sequence[Any], point)
                    if len(row) < 2:
                        continue
                    offset_x = 0.0 if index == 0 else shift_x
                    shifted_path.append(
                        _clip_point_to_bounds(preview_bounds, (float(row[0]) + offset_x, float(row[1])), pad=6)
                    )
                if len(shifted_path) >= 2:
                    path_color = _rgba(color, 214 if kind == "primary" else 176)
                    for start, end in zip(shifted_path, shifted_path[1:]):
                        self._draw_dashed_line(
                            draw,
                            start,
                            end,
                            path_color,
                            width=3 if kind == "primary" else 2,
                            dash=10,
                            gap=7,
                        )
                    end_x, end_y = shifted_path[-1]
                    draw.ellipse(
                        (end_x - 5, end_y - 5, end_x + 5, end_y + 5),
                        fill=path_color,
                        outline=(7, 16, 22, 214),
                        width=1,
                    )

            if kind == "primary" and "invalidation_y" in zone:
                try:
                    invalidation_y = int(round(float(zone.get("invalidation_y", 0.0))))
                except (TypeError, ValueError):
                    invalidation_y = 0
                if 6 <= invalidation_y <= chart_height - 6:
                    self._draw_dashed_line(
                        draw,
                        (chart_width + 10, invalidation_y),
                        (preview_width - 14, invalidation_y),
                        (220, 194, 122, 164),
                        width=2,
                        dash=8,
                        gap=8,
                    )
        return preview.convert("RGB")

    def render_memory_projection_artifacts(
        self,
        artifact_dir: Path,
        artifact_stem: str,
        *,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        projection_payload: Mapping[str, Any],
    ) -> dict[str, str]:
        normalized = _normalize_memory_projection_payload(
            projection_payload,
            mode=str(projection_payload.get("mode", "predict") or "predict"),
        )
        mode = str(normalized.get("mode", "predict") or "predict")
        safe_stem = _slugify(f"{artifact_stem}_{mode}", "projection")
        artifacts: dict[str, str] = {
            "reference_image_path": "",
            "reference_image_name": str(normalized.get("reference_image_name", "") or ""),
            "projection_image_path": "",
        }

        reference_path = self._memory_projection_reference_path(normalized)
        if reference_path is not None:
            try:
                with Image.open(reference_path) as image:
                    reference_image = image.convert("RGB")
                copied_reference_path = artifact_dir / f"{safe_stem}_memory_reference.png"
                _encode_png(reference_image, copied_reference_path)
                artifacts["reference_image_path"] = str(copied_reference_path)
                artifacts["reference_image_name"] = str(reference_path.name)
            except Exception as exc:
                LOGGER.warning("Could not prepare memory reference image %s: %s", reference_path, exc)

        try:
            projection_image = self._render_memory_projection_preview(
                surface_image,
                tracking_summary,
                latest_signal,
                normalized,
            )
            projection_path = artifact_dir / f"{safe_stem}_projection.png"
            _encode_png(projection_image.convert("RGB"), projection_path)
            artifacts["projection_image_path"] = str(projection_path)
        except Exception as exc:
            LOGGER.warning("Could not render memory projection preview for %s: %s", mode, exc)
        return artifacts

    def build_memory_projection(
        self,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        *,
        mode: str = "predict",
        session_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_mode = "future" if str(mode or "").strip().lower() == "future" else "predict"
        retrieval_started_at = _now_iso()
        chart_image = self._chart_image_from_tracking_summary(surface_image, tracking_summary)
        candles: list[dict[str, Any]] = _sequence_of_mappings(tracking_summary.get("tracked_candles", []))
        if not candles:
            candles = self._extract_candle_tracks(chart_image)
        decision_state = self._phoenixguard_decision_state(tracking_summary, latest_signal)
        opportunity_timing = self._phoenixguard_opportunity_timing(
            tracking_summary,
            latest_signal,
            decision_state=decision_state,
        )
        direction = self._phoenixguard_direction(tracking_summary, latest_signal)
        counter_side = _opposite_action(direction)
        bank = self._get_phoenixguard_memory_bank()
        if bank is None:
            payload = _default_memory_projection_payload(
                mode=normalized_mode,
                message="PhoenixGuard memory bank is unavailable for projection.",
                status="degraded",
            )
            payload["memory_retrieval"] = {
                "state": "unavailable",
                "message": "Memory bank did not load. Rechecking metadata/index on the next projection request.",
                "bank_loaded": False,
                "entries": 0,
                "started_at": retrieval_started_at,
                "completed_at": _now_iso(),
            }
            return payload
        query_payload = self._phoenixguard_query_chart_state(chart_image, tracking_summary, latest_signal, candles)
        query_embed = query_payload.get("query_embed")
        if query_embed is None:
            payload = _default_memory_projection_payload(
                mode=normalized_mode,
                message="Live memory query embedding is unavailable for the current chart.",
                status="degraded",
            )
            payload["memory_retrieval"] = {
                "state": "degraded",
                "message": "Live chart embedding could not be built for memory comparison.",
                "bank_loaded": True,
                "entries": int(len(getattr(bank, "entries", []) or [])),
                "started_at": retrieval_started_at,
                "completed_at": _now_iso(),
            }
            return payload
        chart_state = _mapping_to_dict(query_payload.get("chart_state", {}))
        query_context = cast(Mapping[str, Any], query_payload.get("query_context", {}))
        macro_trend = str(query_payload.get("macro_trend", "") or "")
        local_phase = str(query_payload.get("local_phase", "") or "")

        primary_rows = self._score_memory_side_matches(
            bank,
            np.asarray(query_embed, dtype=np.float32),
            desired_label=direction,
            macro_trend=macro_trend,
            local_phase=local_phase,
            chart_state=chart_state,
            query_context=query_context,
            limit=5,
        )
        counter_rows = self._score_memory_side_matches(
            bank,
            np.asarray(query_embed, dtype=np.float32),
            desired_label=counter_side,
            macro_trend=macro_trend,
            local_phase=local_phase,
            chart_state=chart_state,
            query_context=query_context,
            limit=4,
        )
        if not primary_rows:
            payload = _default_memory_projection_payload(
                mode=normalized_mode,
                message="No strong PhoenixGuard memory fit was available for the current live side.",
                status="degraded",
            )
            payload["memory_retrieval"] = {
                "state": "degraded",
                "message": "Memory bank loaded, but no high-quality side match passed retrieval.",
                "bank_loaded": True,
                "entries": int(len(getattr(bank, "entries", []) or [])),
                "started_at": retrieval_started_at,
                "completed_at": _now_iso(),
            }
            return payload
        primary_fit = self._memory_fit_payload(bank, primary_rows, side=direction)
        counter_fit = self._memory_fit_payload(bank, counter_rows, side=counter_side)

        primary_top = _mapping_to_dict(cast(Sequence[Any], primary_fit.get("top_matches", []))[0]) if cast(Sequence[Any], primary_fit.get("top_matches", [])) else {}
        counter_top = _mapping_to_dict(cast(Sequence[Any], counter_fit.get("top_matches", []))[0]) if cast(Sequence[Any], counter_fit.get("top_matches", [])) else {}
        prediction_stack = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], primary_fit.get("top_predictions", []))
            if isinstance(item, Mapping)
        ][:3]
        precision_payload = self._memory_precision_payload(primary_fit, counter_fit)
        if not bool(precision_payload.get("accepted", False)):
            rejected = _default_memory_projection_payload(
                mode=normalized_mode,
                status="degraded",
                message=str(precision_payload.get("reason", "Memory precision gate rejected the current analog.")),
            )
            rejected.update(
                {
                    "timeframe": str(latest_signal.get("focus_timeframe", tracking_summary.get("detected_timeframe", "")) or ""),
                    "market": str(latest_signal.get("market", tracking_summary.get("detected_market", "")) or ""),
                    "dominant_side": direction,
                    "counter_side": counter_side,
                    "decision_state": decision_state,
                    "trade_bias": "HOLD",
                    "memory_similarity": round(float(precision_payload.get("primary_similarity", 0.0) or 0.0), 4),
                    "memory_precision_score": round(float(precision_payload.get("primary_precision", 0.0) or 0.0), 4),
                    "memory_edge": round(float(precision_payload.get("edge", 0.0) or 0.0), 4),
                    "memory_retrieval": {
                        "state": "degraded",
                        "message": str(precision_payload.get("reason", "Memory precision gate rejected the current analog.")),
                        "bank_loaded": True,
                        "entries": int(len(getattr(bank, "entries", []) or [])),
                        "started_at": retrieval_started_at,
                        "completed_at": _now_iso(),
                    },
                    "memory_precision": precision_payload,
                    "primary_fit": primary_fit,
                    "counter_fit": counter_fit,
                    "prediction_stack": prediction_stack,
                }
            )
            return rejected
        memory_match = {
            "dominant_memory_side": direction,
            "top_matches": cast(Sequence[Any], primary_fit.get("top_matches", [])),
            "how_current_differs": str(counter_fit.get("summary", "") or ""),
            "historical_next_event": str(primary_fit.get("summary", "") or ""),
            "historical_next_event_bias": _mapping_to_dict(primary_fit.get("transition_bias", {})),
        }
        live_forward = self._phoenixguard_forward_projection(
            tracking_summary,
            latest_signal,
            memory_match,
            decision_state=decision_state,
            opportunity_timing=opportunity_timing,
        )
        next_candle_bias = _upper_action(_mapping_to_dict(tracking_summary.get("decision_kernel", {})).get("next_candle_bias"), fallback="HOLD")
        primary_transition_bias = _mapping_to_dict(primary_fit.get("transition_bias", {}))
        dominant_event = ""
        dominant_probability = 0.0
        if primary_transition_bias:
            dominant_event, dominant_probability = max(
                ((str(key), float(value)) for key, value in primary_transition_bias.items()),
                key=lambda item: item[1],
            )

        projected_candles: list[dict[str, Any]] = []
        path_steps: list[str] = []
        if next_candle_bias in {"BUY", "SELL"} and next_candle_bias == counter_side and counter_top:
            projected_candles.append(
                {
                    "step": 1,
                    "side": counter_side,
                    "role": "counter_probe",
                    "template": str(counter_top.get("image_name", "counter fit") or "counter fit"),
                    "confidence": round(float(counter_top.get("similarity", 0.0) or 0.0), 4),
                }
            )
            path_steps.append(
                f"Counter {counter_side.lower()} probe can print first, matching {str(counter_top.get('image_name', 'counter fit'))} before the main thesis resolves."
            )
        projected_candles.append(
            {
                "step": len(projected_candles) + 1,
                "side": direction,
                "role": "trigger",
                "template": str(primary_top.get("image_name", "memory fit") or "memory fit"),
                "confidence": round(float(primary_top.get("similarity", 0.0) or 0.0), 4),
            }
        )
        path_steps.append(
            f"{direction} trigger should align with {str(primary_top.get('image_name', 'memory fit'))} and {self._memory_wick_signature(chart_state)}."
        )
        if prediction_stack:
            stack_text = " | ".join(
                f"#{int(row.get('rank', 0) or 0)} {str(row.get('image_name', 'memory'))} p{float(row.get('precision', 0.0) or 0.0):.2f}"
                for row in prediction_stack[:3]
            )
            path_steps.append(f"Top memory forecast stack: {stack_text}.")
        projected_candles.append(
            {
                "step": len(projected_candles) + 1,
                "side": direction,
                "role": dominant_event or "continuation",
                "template": str(primary_top.get("image_name", "memory path") or "memory path"),
                "confidence": round(float(dominant_probability or primary_top.get("similarity", 0.0) or 0.0), 4),
            }
        )
        if dominant_event:
            path_steps.append(
                f"Closest {direction.lower()} memory favored {_friendly_phrase(dominant_event)} {dominant_probability:.0%} of the time after the trigger."
            )
        if counter_top:
            counter_gap = float(primary_top.get("similarity", 0.0) or 0.0) - float(counter_top.get("similarity", 0.0) or 0.0)
            path_steps.append(
                f"Counter side stays secondary while the memory edge remains {counter_gap:+.2f} above {str(counter_top.get('image_name', 'counter fit'))}."
            )
        counter_behavior = _mapping_to_dict(precision_payload.get("counter_behavior", {}))
        if counter_behavior:
            path_steps.append(str(counter_behavior.get("message", "") or "Counter behavior checked against opposite-side memory."))
        if normalized_mode == "future":
            projected_candles.append(
                {
                    "step": len(projected_candles) + 1,
                    "side": direction,
                    "role": "target_or_pause",
                    "template": str(primary_top.get("image_name", "memory projection") or "memory projection"),
                    "confidence": round(float(max(dominant_probability, float(primary_top.get("similarity", 0.0) or 0.0) * 0.9)), 4),
                }
            )
            path_steps.append(
                "Future path stays memory-backed until trigger, target, or invalidation breaks the current analog."
            )

        headline = (
            f"{direction} {decision_state} | "
            f"{str(primary_top.get('image_name', 'memory fit'))} "
            f"sim {float(primary_top.get('similarity', 0.0) or 0.0):.2f} "
            f"precision {float(primary_top.get('precision_score', 0.0) or 0.0):.2f} is the tightest PhoenixGuard memory fit."
        ).strip()
        summary = (
            f"{headline} Counter check: "
            f"{str(counter_top.get('image_name', 'none')) if counter_top else 'none'} "
            f"{float(counter_top.get('similarity', 0.0) or 0.0):.2f}" if counter_top else headline
        )
        forward_projection = {
            "headline": headline,
            "path": (
                f"{str(live_forward.get('likely_path', '') or '').strip()} "
                f"Memory fit says {_friendly_phrase(dominant_event, fallback='continuation')} is the highest-probability release."
            ).strip(),
            "trigger_area": str(live_forward.get("likely_trigger_area", "") or ""),
            "target_area": str(live_forward.get("likely_target_area", "") or ""),
            "invalidation_area": str(live_forward.get("likely_invalidation_area", "") or ""),
            "immediacy": str(live_forward.get("immediacy", "forming") or "forming"),
            "projected_candles": projected_candles,
            "path_steps": path_steps,
        }
        hotspots = self._memory_projection_hotspots(
            tracking_summary,
            dominant_side=direction,
            primary_fit=primary_fit,
            counter_fit=counter_fit,
        )
        stacked_accept = str(precision_payload.get("accepted_by", "") or "") == "stacked_favor"
        live_permission = str(latest_signal.get("execution_permission", "WAIT") or "WAIT").upper()
        memory_execution_permission = live_permission
        if stacked_accept and live_permission in {"WAIT", "WAIT_FOR_CONFIRMATION", "WAIT_FOR_TRIGGER", "WAIT_FOR_SNIPER"}:
            memory_execution_permission = "AGGRESSIVE_WATCH"
        if bool(latest_signal.get("actionable")) and decision_state in {"triggering", "active"}:
            memory_execution_permission = "EXECUTE"
        memory_actionable = (
            bool(latest_signal.get("actionable")) and decision_state in {"armed", "triggering", "active"}
        ) or (stacked_accept and decision_state in {"armed", "triggering", "active"})
        payload = _default_memory_projection_payload(mode=normalized_mode)
        payload.update(
            {
                "status": "ready" if primary_rows else "degraded",
                "summary": summary,
                "timeframe": str(latest_signal.get("focus_timeframe", tracking_summary.get("detected_timeframe", "")) or ""),
                "market": str(latest_signal.get("market", tracking_summary.get("detected_market", "")) or ""),
                "dominant_side": direction,
                "counter_side": counter_side,
                "decision_state": decision_state,
                "trade_bias": direction,
                "execution_permission": memory_execution_permission,
                "actionable": bool(memory_actionable),
                "memory_similarity": round(float(primary_top.get("similarity", 0.0) or 0.0), 4),
                "memory_precision_score": round(float(precision_payload.get("primary_precision", 0.0) or 0.0), 4),
                "memory_edge": round(float(precision_payload.get("edge", 0.0) or 0.0), 4),
                "memory_retrieval": {
                    "state": "ready",
                    "message": "Memory retrieval complete. Top three forecasts scored against live candle structure.",
                    "bank_loaded": True,
                    "entries": int(len(getattr(bank, "entries", []) or [])),
                    "started_at": retrieval_started_at,
                    "completed_at": _now_iso(),
                },
                "memory_precision": precision_payload,
                "memory_direction": _upper_action(primary_top.get("label", direction), fallback=direction),
                "reference_image_name": str(primary_top.get("image_name", "") or ""),
                "model_council": {
                    "live_side": direction,
                    "decision_state": decision_state,
                    "primary_memory_fit": str(primary_top.get("image_name", "") or ""),
                    "primary_similarity": round(float(primary_top.get("similarity", 0.0) or 0.0), 4),
                    "counter_memory_fit": str(counter_top.get("image_name", "") or ""),
                    "counter_similarity": round(float(counter_top.get("similarity", 0.0) or 0.0), 4),
                    "primary_precision": round(float(precision_payload.get("primary_precision", 0.0) or 0.0), 4),
                    "counter_precision": round(float(precision_payload.get("counter_precision", 0.0) or 0.0), 4),
                    "dominant_transition_bias": primary_transition_bias,
                    "counter_transition_bias": _mapping_to_dict(counter_fit.get("transition_bias", {})),
                    "counter_behavior": counter_behavior,
                    "accepted_by": str(precision_payload.get("accepted_by", "") or ""),
                    "memory_edge": round(float(precision_payload.get("edge", 0.0) or 0.0), 4),
                    "precision_edge": round(float(precision_payload.get("precision_edge", 0.0) or 0.0), 4),
                },
                "primary_fit": primary_fit,
                "counter_fit": counter_fit,
                "prediction_stack": prediction_stack,
                "forward_projection": forward_projection,
                "hotspots": hotspots,
            }
        )
        return payload

    def _detect_chart_bbox(self, image: Image.Image) -> tuple[list[int], float]:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[0] < 48 or arr.shape[1] < 48:
            return [0, 0, int(image.width), int(image.height)], 0.0
        green_mask, sell_mask = self._build_candle_masks(arr)
        union_mask = self._build_candle_like_union_mask(green_mask, sell_mask, image.size)
        chart_mask = union_mask.copy()
        top_strip_bottom = max(28, int(round(image.height * 0.085)))
        if int(np.sum(chart_mask[top_strip_bottom:, :] > 0)) >= 12:
            chart_mask[:top_strip_bottom, :] = 0
        bbox = _binary_content_bbox(chart_mask)
        if bbox is None:
            return [0, 0, int(image.width), int(image.height)], 0.0
        x0, y0, x1, y1 = bbox
        pad_x = max(10, int(round(image.width * 0.028)))
        pad_y = max(10, int(round(image.height * 0.05)))
        expanded = _expand_bbox(image.size, [x0, y0, x1, y1], pad_x=pad_x, pad_y=pad_y)
        panel_limit = self._detect_order_panel_left_boundary(arr)
        if panel_limit is not None and panel_limit > int(image.width * 0.55):
            expanded[2] = max(expanded[0] + 1, min(int(expanded[2]), int(panel_limit - max(8, pad_x // 2))))
        coverage = float((max(1, x1 - x0) * max(1, y1 - y0)) / max(1, image.width * image.height))
        confidence = _clip01(0.32 + coverage * 1.8)
        return expanded, confidence

    def _detect_order_panel_left_boundary(self, arr: ArrayND) -> int | None:
        try:
            detector = PocketOptionBrokerExecutionBackend()
            buy = _mapping_to_dict(detector._find_button(arr, "BUY"))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            sell = _mapping_to_dict(detector._find_button(arr, "SELL"))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        except Exception:
            return None
        if not buy or not sell:
            return None
        try:
            buy_bbox = cast(Sequence[Any], buy.get("bbox", []))
            sell_bbox = cast(Sequence[Any], sell.get("bbox", []))
            if len(buy_bbox) < 4 or len(sell_bbox) < 4:
                return None
            buy_x0 = int(round(float(buy_bbox[0])))
            sell_x0 = int(round(float(sell_bbox[0])))
            if _clip01(buy.get("confidence", 0.0)) < 0.45 or _clip01(sell.get("confidence", 0.0)) < 0.45:
                return None
            return min(buy_x0, sell_x0)
        except Exception:
            return None

    def _build_candle_masks(self, arr: ArrayND) -> tuple[ArrayND, ArrayND]:
        rgb = np.asarray(arr, dtype=np.uint8)
        red = rgb[:, :, 0].astype(np.int16)
        green = rgb[:, :, 1].astype(np.int16)
        blue = rgb[:, :, 2].astype(np.int16)
        green_mask = np.where(
            (green >= 95) & (green >= red + 18) & (green >= blue + 12),
            255,
            0,
        ).astype(np.uint8)
        sell_mask = np.where(
            (red >= 120) & (blue >= 90) & (red >= green + 26) & (blue >= green + 10),
            255,
            0,
        ).astype(np.uint8)
        try:
            import cv2  # type: ignore[import-not-found]

            kernel = np.ones((3, 3), dtype=np.uint8)
            green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
            sell_mask = cv2.morphologyEx(sell_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        except Exception:
            pass
        return green_mask, sell_mask

    def _build_candle_like_union_mask(
        self,
        green_mask: ArrayND,
        sell_mask: ArrayND,
        image_size: tuple[int, int],
    ) -> ArrayND:
        union_mask = np.where((green_mask > 0) | (sell_mask > 0), 255, 0).astype(np.uint8)
        if union_mask.size == 0:
            return union_mask
        try:
            import cv2  # type: ignore[import-not-found]

            component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(union_mask, connectivity=8)
            cleaned = np.zeros_like(union_mask)
            image_width = max(1, int(image_size[0]))
            image_height = max(1, int(image_size[1]))
            max_body_width = max(32, int(round(image_width * 0.045)))
            max_component_area = max(90, int(round(image_width * image_height * 0.012)))
            min_component_height = max(4, int(round(image_height * 0.006)))
            for label_index in range(1, int(component_count)):
                _x = int(stats[label_index, cv2.CC_STAT_LEFT])
                y = int(stats[label_index, cv2.CC_STAT_TOP])
                width = int(stats[label_index, cv2.CC_STAT_WIDTH])
                height = int(stats[label_index, cv2.CC_STAT_HEIGHT])
                area = int(stats[label_index, cv2.CC_STAT_AREA])
                if area < 5 or height < min_component_height:
                    continue
                wide_control = width > max_body_width and (area > max_component_area or width > height * 2.25)
                top_navigation_chip = y < image_height * 0.12 and width > max_body_width
                bottom_navigation_chip = y > image_height * 0.94 and width > max_body_width
                if wide_control or top_navigation_chip or bottom_navigation_chip:
                    continue
                cleaned[labels == label_index] = 255
            if int(np.sum(cleaned > 0)) >= 12:
                return cleaned
        except Exception:
            LOGGER.debug("Candle-like mask cleanup failed; using raw color mask.", exc_info=True)
        return union_mask

    def _extract_candle_tracks(self, image: Image.Image) -> list[dict[str, Any]]:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[0] < 32 or arr.shape[1] < 32:
            return []
        green_mask, sell_mask = self._build_candle_masks(arr)
        union_mask = self._build_candle_like_union_mask(green_mask, sell_mask, image.size)
        x_profile = np.sum(union_mask > 0, axis=0).astype(np.int32)
        active_columns = np.where(
            x_profile >= max(3, int(round(float(max(1, x_profile.max())) * 0.18))),
            1,
            0,
        ).astype(np.uint8)
        groups = _group_column_runs(active_columns, max_gap=2)
        tracks: list[dict[str, Any]] = []
        height = max(1, int(image.height))
        width = max(1, int(image.width))
        for index, (x0, x1) in enumerate(groups, start=1):
            if (x1 - x0) < 2:
                continue
            slice_mask = union_mask[:, x0:x1]
            ys, _xs = np.where(slice_mask > 0)
            if ys.size < 5:
                continue
            y0 = int(ys.min())
            y1 = int(ys.max()) + 1
            green_count = int(np.sum(green_mask[:, x0:x1] > 0))
            sell_count = int(np.sum(sell_mask[:, x0:x1] > 0))
            if green_count == 0 and sell_count == 0:
                continue
            direction = "BUY" if green_count >= sell_count else "SELL"
            color = "green" if direction == "BUY" else "magenta"
            center_x = float((x0 + x1) * 0.5)
            center_y = float((y0 + y1) * 0.5)
            price_proxy = float(1.0 - (center_y / max(1.0, float(height - 1))))
            tracks.append(
                {
                    "track_id": int(index),
                    "bbox": [int(x0), int(y0), int(x1), int(y1)],
                    "center_x": center_x,
                    "center_y": center_y,
                    "price_proxy": price_proxy,
                    "direction": direction,
                    "color": color,
                    "width": int(x1 - x0),
                    "height": int(y1 - y0),
                    "body_height_pct": float((y1 - y0) / max(1.0, float(height))),
                    "normalized_x": float(center_x / max(1.0, float(width))),
                    "normalized_y": float(center_y / max(1.0, float(height))),
                }
            )
        return self._filter_main_candle_tracks(tracks, image.size)

    def _filter_main_candle_tracks(
        self,
        tracks: Sequence[Mapping[str, Any]],
        image_size: tuple[int, int],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = [dict(item) for item in tracks]
        if len(rows) < 6:
            return rows
        image_width = max(1, int(image_size[0]))
        image_height = max(1, int(image_size[1]))
        top_strip_bottom = max(30.0, float(image_height) * 0.085)
        bottom_strip_top = float(image_height) * 0.985
        candidates: list[dict[str, Any]] = []
        for row in rows:
            bbox = cast(Sequence[Any], row.get("bbox", []))
            if len(bbox) < 4:
                continue
            x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
            track_width = max(1.0, x1 - x0)
            track_height = max(1.0, y1 - y0)
            center_y = float(row.get("center_y", (y0 + y1) * 0.5) or (y0 + y1) * 0.5)
            if y1 <= top_strip_bottom and track_height <= image_height * 0.10:
                continue
            if y0 >= bottom_strip_top and track_height <= image_height * 0.06:
                continue
            if track_width > image_width * 0.09 and track_height < image_height * 0.09:
                continue
            row["center_y"] = center_y
            candidates.append(row)
        if len(candidates) < 5:
            return rows

        heights = np.asarray([float(item.get("height", 0.0) or 0.0) for item in candidates], dtype=np.float32)
        median_height = float(np.median(heights)) if heights.size else 0.0
        min_height = max(5.0, min(float(image_height) * 0.028, median_height * 0.42))
        filtered = [row for row in candidates if float(row.get("height", 0.0) or 0.0) >= min_height]
        if len(filtered) < 5:
            filtered = candidates

        filtered.sort(key=lambda item: float(item.get("center_x", 0.0) or 0.0))
        return filtered

    def _derive_support_resistance_zones(
        self,
        candles: Sequence[Mapping[str, Any]],
        image_size: tuple[int, int],
        *,
        candidate_action: str,
    ) -> list[dict[str, Any]]:
        if len(candles) < 6:
            return []
        width, height = int(image_size[0]), int(image_size[1])
        lows: list[dict[str, float]] = []
        highs: list[dict[str, float]] = []
        ranges: list[float] = []
        for candle in candles:
            bbox = cast(Sequence[Any], candle.get("bbox", []))
            if len(bbox) < 4:
                continue
            x0 = float(bbox[0])
            x1 = float(bbox[2])
            top = min(float(bbox[1]), float(bbox[3]))
            bottom = max(float(bbox[1]), float(bbox[3]))
            center_x = float(candle.get("center_x", (x0 + x1) * 0.5) or (x0 + x1) * 0.5)
            highs.append({"y": top, "x0": min(x0, x1), "x1": max(x0, x1), "center_x": center_x})
            lows.append({"y": bottom, "x0": min(x0, x1), "x1": max(x0, x1), "center_x": center_x})
            ranges.append(max(1.0, bottom - top))
        if not highs or not lows:
            return []
        latest_bbox = cast(Sequence[Any], candles[-1].get("bbox", []))
        latest_center = (
            float(candles[-1].get("center_y", height * 0.5) or height * 0.5)
            if len(latest_bbox) < 4
            else (min(float(latest_bbox[1]), float(latest_bbox[3])) + max(float(latest_bbox[1]), float(latest_bbox[3]))) * 0.5
        )
        median_range = float(np.median(np.asarray(ranges, dtype=np.float32))) if ranges else max(8.0, height * 0.03)
        merge_band = max(5.0, min(float(height) * 0.055, median_range * 0.58))
        zone_half_height = max(3.0, merge_band * 0.42)
        candle_widths = [
            max(1.0, float(item.get("x1", 0.0)) - float(item.get("x0", 0.0)))
            for item in highs + lows
        ]
        median_candle_width = float(np.median(np.asarray(candle_widths, dtype=np.float32))) if candle_widths else max(6.0, width * 0.012)
        zone_pad_x = max(14.0, min(float(width) * 0.055, median_candle_width * 3.2))
        min_zone_width = max(44.0, min(float(width) * 0.18, median_candle_width * 9.0))

        def clusters(points: Sequence[Mapping[str, float]], role: str) -> list[dict[str, Any]]:
            sorted_points = sorted(points, key=lambda item: float(item.get("y", 0.0)))
            groups: list[list[dict[str, float]]] = []
            for point in sorted_points:
                point_y = float(point.get("y", 0.0))
                if groups:
                    group_y = float(np.mean(np.asarray([float(item.get("y", 0.0)) for item in groups[-1]], dtype=np.float32)))
                else:
                    group_y = point_y
                if groups and abs(point_y - group_y) <= merge_band:
                    groups[-1].append(dict(point))
                else:
                    groups.append([dict(point)])
            rows: list[dict[str, Any]] = []
            for group in groups:
                center = float(np.mean(np.asarray([float(item.get("y", 0.0)) for item in group], dtype=np.float32)))
                touch_count = len(group)
                distance_px = abs(center - latest_center)
                distance_norm = _clip01(distance_px / max(1.0, float(height)))
                proximity = _clip01(1.0 - distance_px / max(1.0, height * 0.42))
                span_left = min(float(item.get("x0", 0.0)) for item in group)
                span_right = max(float(item.get("x1", span_left + 1.0)) for item in group)
                span_center = float(
                    np.mean(np.asarray([float(item.get("center_x", (span_left + span_right) * 0.5)) for item in group], dtype=np.float32))
                )
                recency = _clip01(span_center / max(1.0, float(width)))
                strength = _clip01(
                    0.42 * min(1.0, touch_count / 4.0)
                    + 0.24 * proximity
                    + 0.18 * recency
                    + 0.16 * (1.0 - abs(center - height * 0.5) / max(1.0, height * 0.5))
                )
                if strength < 0.30:
                    continue
                top = int(round(max(0.0, center - zone_half_height)))
                bottom = int(round(min(float(height), center + zone_half_height)))
                left = max(float(width) * 0.03, span_left - zone_pad_x)
                right = min(float(width) * 0.97, span_right + zone_pad_x)
                if (right - left) < min_zone_width:
                    half = min_zone_width * 0.5
                    left = max(float(width) * 0.03, span_center - half)
                    right = min(float(width) * 0.97, span_center + half)
                if right <= left + 8.0:
                    right = min(float(width) * 0.97, left + min_zone_width)
                if center > latest_center + zone_half_height:
                    relation = "below_price"
                elif center < latest_center - zone_half_height:
                    relation = "above_price"
                else:
                    relation = "at_price"
                candidate_side = _upper_action(candidate_action)
                if candidate_side == "BUY":
                    relevance = "entry_support" if role == "support" and relation in {"below_price", "at_price"} else "target_resistance" if role == "resistance" and relation == "above_price" else "context"
                elif candidate_side == "SELL":
                    relevance = "entry_resistance" if role == "resistance" and relation in {"above_price", "at_price"} else "target_support" if role == "support" and relation == "below_price" else "context"
                else:
                    relevance = "context"
                rows.append(
                    {
                        "key": f"{role}_{len(rows) + 1}",
                        "role": role,
                        "label": f"{role.upper()} {touch_count}T",
                        "direction": "BUY" if role == "support" else "SELL",
                        "bbox": [int(round(left)), top, int(round(right)), max(top + 1, bottom)],
                        "line_y": int(round(center)),
                        "line_x0": int(round(left)),
                        "line_x1": int(round(right)),
                        "zone_height_px": int(max(1, round(bottom - top))),
                        "touch_count": int(touch_count),
                        "touch_points": [[int(round(item.get("center_x", 0.0))), int(round(item.get("y", 0.0)))] for item in group],
                        "distance_to_latest_px": round(float(distance_px), 3),
                        "distance_to_latest_norm": round(float(distance_norm), 4),
                        "price_relation": relation,
                        "entry_relevance": relevance,
                        "recency_score": round(float(recency), 4),
                        "confidence": round(float(strength), 4),
                        "candidate_side": candidate_side,
                        "nearest": False,
                    }
                )
            rows.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
            return rows[:2]

        zones = clusters(lows, "support") + clusters(highs, "resistance")
        for role in ("support", "resistance"):
            role_zones = [zone for zone in zones if str(zone.get("role", "")) == role]
            if role_zones:
                nearest = min(role_zones, key=lambda item: float(item.get("distance_to_latest_norm", 1.0)))
                nearest["nearest"] = True
                nearest["label"] = f"NEAREST {str(nearest.get('label', role.upper()))}"
        zones.sort(key=lambda item: (0 if bool(item.get("nearest", False)) else 1, str(item.get("role", "")), -float(item.get("confidence", 0.0))))
        return zones[:4]

    def _build_signal_payloads(
        self,
        chart_image: Image.Image,
        chart_region: Mapping[str, Any],
        tracked_candles: Sequence[Mapping[str, Any]],
        timeframe_selector: Mapping[str, Any],
        *,
        market_selector: Mapping[str, Any] | None = None,
        session_payload: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        candles = [dict(item) for item in tracked_candles]
        timeframe = str(timeframe_selector.get("value", "") or "").upper()
        timeframe_source = str(timeframe_selector.get("source", "unconfirmed") or "unconfirmed")
        timeframe_confidence = _clip01(timeframe_selector.get("confidence", 0.0))
        market_row = _mapping_to_dict(market_selector)
        market = _normalize_fx_market_candidate(market_row.get("value", ""))
        market_source = str(market_row.get("source", "unconfirmed") or "unconfirmed")
        market_confidence = _clip01(market_row.get("confidence", 0.0)) if market else 0.0
        if len(candles) < 5:
            tracking = _default_tracking_summary(message="Waiting for more visible candle structure.")
            tracking["chart_region"] = dict(chart_region)
            tracking["display_region"] = dict(chart_region)
            tracking["tracked_candles"] = candles
            tracking["detected_timeframe"] = timeframe
            tracking["timeframe_source"] = timeframe_source
            tracking["timeframe_confidence"] = timeframe_confidence
            tracking["detected_market"] = market
            tracking["market_source"] = market_source
            tracking["market_confidence"] = market_confidence
            signal = _default_signal(
                message="Waiting for more visible candle structure inside the locked focus region.",
                status="warming",
            )
            signal["focus_timeframe"] = timeframe
            signal["focus_timeframe_source"] = timeframe_source
            signal["market"] = market
            signal["market_source"] = market_source
            signal["market_confidence"] = market_confidence
            return tracking, signal

        proxies = [float(item.get("price_proxy", 0.0)) for item in candles]
        recent_window = min(len(candles), 8)
        global_window = min(len(candles), 18)
        current_window = min(len(candles), 4)
        global_values = proxies[-global_window:]
        local_values = proxies[-recent_window:]
        current_values = proxies[-current_window:]

        global_slope = _regression_slope(global_values)
        local_slope = _regression_slope(local_values)
        impulse_delta = float(proxies[-1] - proxies[-2])
        current_slope = _regression_slope(current_values)
        global_direction = _trend_direction(global_slope, epsilon=0.018)
        local_direction = _trend_direction(local_slope, epsilon=0.018)
        impulse_direction = _trend_direction(impulse_delta, epsilon=0.022)
        latest_direction = str(candles[-1].get("direction", "HOLD") or "HOLD")
        latest_color = str(candles[-1].get("color", "unknown") or "unknown")
        latest_body_height_pct = _clip01(candles[-1].get("body_height_pct", 0.0))
        recent_range = max(local_values) - min(local_values)
        consolidation_score = _clip01(max(0.0, 0.18 - recent_range) / 0.18)
        continuation_score = _clip01((abs(global_slope) + abs(local_slope)) * 2.8)
        impulse_score = _clip01(abs(impulse_delta) * 8.0 + latest_body_height_pct * 1.8)
        reversal_score = _clip01(max(0.0, abs(local_slope) - abs(global_slope)) * 2.4)

        setup = ""
        candidate_action = "HOLD"
        if consolidation_score >= 0.72 and abs(local_slope) <= 0.03:
            setup = "CONSOLIDATION"
            candidate_action = "HOLD"
        elif global_direction == local_direction and global_direction in {"BUY", "SELL"}:
            if impulse_direction == global_direction and impulse_score >= 0.56:
                setup = f"IMPULSE {global_direction}"
            else:
                setup = f"CONTINUATION {global_direction}"
            candidate_action = global_direction
        elif local_direction in {"BUY", "SELL"} and impulse_direction == local_direction and reversal_score >= 0.30:
            setup = f"REVERSAL ATTEMPT {local_direction}"
            candidate_action = local_direction
        elif latest_direction in {"BUY", "SELL"} and impulse_score >= 0.42:
            setup = f"CURRENT PRESSURE {latest_direction}"
            candidate_action = latest_direction
        else:
            setup = "NEUTRAL STUDY"
            candidate_action = "HOLD"

        confidence = _clip01(
            0.26 * _clip01(len(candles) / 16.0)
            + 0.22 * continuation_score
            + 0.22 * impulse_score
            + 0.16 * _clip01(abs(local_slope) * 5.0)
            + 0.14 * _clip01(chart_region.get("confidence", 0.0))
        )

        global_box = self._structure_box(candles[-global_window:], chart_image.size, "global", "GLOBAL", direction=global_direction)
        local_box = self._structure_box(candles[-recent_window:], chart_image.size, "local", "LOCAL", direction=local_direction)
        current_box = self._structure_box(candles[-current_window:], chart_image.size, "current", "CURRENT", direction=impulse_direction)
        historical_structure = self._build_historical_structure(candles, chart_image.size)
        support_resistance_zones = self._derive_support_resistance_zones(
            candles,
            chart_image.size,
            candidate_action=candidate_action,
        )
        projection = self._build_projection_payload(
            candles,
            chart_image.size,
            candidate_action=candidate_action,
            execution_action="HOLD",
            global_direction=global_direction,
            local_direction=local_direction,
            impulse_direction=impulse_direction,
            confidence=confidence,
            local_slope=local_slope,
            impulse_delta=impulse_delta,
            recent_range=recent_range,
            latest_body_height_pct=latest_body_height_pct,
        )
        entry_plan = self._derive_entry_plan(
            candles,
            projection,
            candidate_action=candidate_action,
            global_direction=global_direction,
            local_direction=local_direction,
            impulse_direction=impulse_direction,
            confidence=confidence,
            latest_body_height_pct=latest_body_height_pct,
        )
        execution_action = str(entry_plan.get("execution_action", "HOLD") or "HOLD").upper()
        execution_permission = str(entry_plan.get("execution_permission", "WAIT") or "WAIT").upper()
        candle_statistics = self._build_candle_statistics(
            candles,
            candidate_action=str(projection.get("direction", candidate_action) or candidate_action).upper(),
        )
        behavior_payload = self._build_behavior_payload(
            candles,
            projection,
            candle_statistics,
            candidate_action=str(projection.get("direction", candidate_action) or candidate_action).upper(),
            global_direction=global_direction,
            local_direction=local_direction,
            impulse_direction=impulse_direction,
            global_slope=global_slope,
            local_slope=local_slope,
            current_slope=current_slope,
            recent_range=recent_range,
            consolidation_score=consolidation_score,
            impulse_score=impulse_score,
            reversal_score=reversal_score,
        )
        probability_payload = self._build_projection_probability_payload(
            candle_statistics,
            projection,
            entry_plan,
            behavior_payload,
            candidate_action=candidate_action,
            global_direction=global_direction,
            local_direction=local_direction,
            impulse_direction=impulse_direction,
            confidence=confidence,
            consolidation_score=consolidation_score,
            continuation_score=continuation_score,
            impulse_score=impulse_score,
            reversal_score=reversal_score,
        )
        decision_kernel = analyze_decision_kernel(
            self._build_decision_kernel_snapshot(
                candles=candles,
                timeframe=timeframe,
                setup=setup,
                projection=projection,
                entry_plan=entry_plan,
                probability_payload=probability_payload,
                behavior_payload=behavior_payload,
                candle_statistics=candle_statistics,
                candidate_action=candidate_action,
                execution_action=execution_action,
                global_direction=global_direction,
                local_direction=local_direction,
                impulse_direction=impulse_direction,
                global_slope=global_slope,
                local_slope=local_slope,
                current_slope=current_slope,
                impulse_delta=impulse_delta,
                confidence=confidence,
                consolidation_score=consolidation_score,
                continuation_score=continuation_score,
                impulse_score=impulse_score,
                reversal_score=reversal_score,
                latest_body_height_pct=latest_body_height_pct,
                session_payload=session_payload,
            )
        )
        kernel_trade_mode = str(decision_kernel.get("trade_mode", "STAND_ASIDE") or "STAND_ASIDE").upper()
        kernel_candle_side = _upper_action(decision_kernel.get("candle_execution_side", "HOLD"))
        execution_controls = _normalize_execution_controls(
            _mapping_to_dict((session_payload or {}).get("execution_controls", {}))
            if session_payload is not None
            else {}
        )
        countertrend_allowed = bool(execution_controls.get("allow_countertrend_scalp", False))
        countertrend_lane = {
            "state": (
                "SCALP_READY"
                if countertrend_allowed and kernel_trade_mode == "COUNTERTREND_SCALP" and kernel_candle_side in {"BUY", "SELL"}
                else "DISABLED"
                if not countertrend_allowed and kernel_trade_mode == "COUNTERTREND_SCALP"
                else "WARNING"
                if kernel_trade_mode == "PULLBACK_WAIT"
                else "INACTIVE"
            ),
            "side": (
                kernel_candle_side
                if countertrend_allowed and kernel_trade_mode == "COUNTERTREND_SCALP"
                else _upper_action(decision_kernel.get("countertrend_side", "HOLD"))
                if countertrend_allowed
                else "HOLD"
            ),
            "trade_mode": kernel_trade_mode,
            "window_candles": int(decision_kernel.get("countertrend_window_candles", 0) or 0),
            "actionable": bool(
                countertrend_allowed
                and kernel_trade_mode == "COUNTERTREND_SCALP"
                and kernel_candle_side in {"BUY", "SELL"}
            ),
            "instruction": (
                str(decision_kernel.get("candle_instruction", "") or decision_kernel.get("market_conversation", "") or "")
                if countertrend_allowed
                else "Countertrend scalp is disabled; PhoenixGuard will not arm an opposite-side scalp against the live trend stack."
            ),
        }
        projection["entry_state"] = str(entry_plan.get("entry_state", "WAIT") or "WAIT")
        projection["entry_label"] = str(entry_plan.get("entry_label", "WAIT") or "WAIT")
        projection["entry_quality"] = str(entry_plan.get("entry_quality", "NONE") or "NONE")
        projection["timing_score"] = _clip01(entry_plan.get("timing_score", 0.0))
        projection.update(probability_payload)
        projection["behavior_state"] = str(behavior_payload.get("current_state", "noise") or "noise")
        projection["next_behavior_state"] = str(behavior_payload.get("next_most_likely_state", "sideways_pause") or "sideways_pause")
        projection["decision_kernel"] = decision_kernel
        tracked_public = candles[-36:]

        reasons = [
            f"global {global_direction} slope {global_slope:+.3f}",
            f"local {local_direction} slope {local_slope:+.3f}",
            f"impulse {impulse_direction} delta {impulse_delta:+.3f}",
            f"visible candles {len(candles)}",
            f"latest candle {latest_color}",
        ]
        behavior_tokens = _sequence_of_mappings(behavior_payload.get("candle_tokens", []))
        latest_token = behavior_tokens[-1] if behavior_tokens else {}
        entry_distance = {
            "sniper": _clip01(latest_token.get("distance_to_sniper", 1.0)),
            "trigger": _clip01(latest_token.get("distance_to_trigger", 1.0)),
            "target": _clip01(latest_token.get("distance_to_target", 1.0)),
            "invalidation": _clip01(latest_token.get("distance_to_invalidation", 1.0)),
        }
        kernel_bias = _upper_action(decision_kernel.get("dominant_side", candidate_action), fallback=candidate_action)
        major_bias = candidate_action if candidate_action in {"BUY", "SELL"} else kernel_bias if kernel_bias in {"BUY", "SELL"} else "HOLD"
        control_state = self._build_global_local_control_payload(
            global_direction=global_direction,
            local_direction=local_direction,
            impulse_direction=impulse_direction,
            global_slope=global_slope,
            local_slope=local_slope,
            current_slope=current_slope,
            impulse_delta=impulse_delta,
            latest_body_height_pct=latest_body_height_pct,
            consolidation_score=consolidation_score,
            continuation_score=continuation_score,
            reversal_score=reversal_score,
            candle_statistics=candle_statistics,
            decision_kernel=decision_kernel,
        )

        tracking_summary = {
            "chart_valid": True,
            "surface_kind": "manual_focus_surface",
            "visible_candle_count": int(len(candles)),
            "active_track_count": int(len(candles)),
            "chart_region": dict(chart_region),
            "display_region": dict(chart_region),
            "detected_timeframe": timeframe,
            "timeframe_source": timeframe_source,
            "timeframe_confidence": timeframe_confidence,
            "detected_market": market,
            "market_source": market_source,
            "market_confidence": market_confidence,
            "global_direction": global_direction,
            "local_direction": local_direction,
            "impulse_direction": impulse_direction,
            "global_slope": global_slope,
            "local_slope": local_slope,
            "current_slope": current_slope,
            "impulse_delta": impulse_delta,
            "latest_candle_color": latest_color,
            "latest_price_proxy": float(proxies[-1]),
            "recent_price_momentum": impulse_delta,
            "overlay_kind": setup,
            "tracked_candles": tracked_public,
            "historical_structure": historical_structure,
            "support_resistance_zones": support_resistance_zones,
            "structure_boxes": [global_box, local_box, current_box],
            "current_box": current_box,
            "projection": projection,
            "entry_state": str(entry_plan.get("entry_state", "WAIT") or "WAIT"),
            "entry_label": str(entry_plan.get("entry_label", "WAIT") or "WAIT"),
            "entry_quality": str(entry_plan.get("entry_quality", "NONE") or "NONE"),
            "candle_statistics": candle_statistics,
            "behavior": behavior_payload,
            "decision_kernel": decision_kernel,
            "countertrend_lane": countertrend_lane,
            "box_context": _mapping_to_dict(behavior_payload.get("box_context", {})),
            "trend_context": _mapping_to_dict(behavior_payload.get("trend_context", {})),
            "consolidation_score": consolidation_score,
            "continuation_score": continuation_score,
            "impulse_score": impulse_score,
            "reversal_score": reversal_score,
            "latest_body_height_pct": latest_body_height_pct,
            "global_local_control": control_state,
            "control_owner": str(control_state.get("owner", "balanced")),
            "control_direction": str(control_state.get("direction", "HOLD")),
            "control_horizon_candles": int(control_state.get("estimated_control_candles", 0) or 0),
        }
        signal = {
            "signal_id": f"tracker_{uuid4().hex}",
            "action": candidate_action,
            "headline_action": candidate_action,
            "candidate_action": candidate_action,
            "model_action": candidate_action,
            "execution_action": execution_action,
            "major_bias": major_bias,
            "bias_direction": major_bias,
            "direction": major_bias,
            "execution_confidence": confidence if execution_action != "HOLD" else confidence * 0.65,
            "confidence": confidence,
            "effective_confidence": confidence,
            "candidate_confidence": confidence,
            "raw_confidence": confidence,
            "status": "tracking",
            "summary": self._signal_summary(
                setup,
                candidate_action,
                execution_action,
                global_direction,
                local_direction,
                entry_state=str(entry_plan.get("entry_state", "WAIT") or "WAIT"),
                entry_instruction=str(entry_plan.get("instruction", "") or ""),
            ),
            "setup": setup,
            "focus_timeframe": timeframe,
            "focus_timeframe_source": timeframe_source,
            "market": market,
            "market_source": market_source,
            "market_confidence": market_confidence,
            "execution_permission": execution_permission,
            "entry_state": str(entry_plan.get("entry_state", "WAIT") or "WAIT"),
            "entry_label": str(entry_plan.get("entry_label", "WAIT") or "WAIT"),
            "entry_stage_label": str(entry_plan.get("entry_stage_label", execution_permission) or execution_permission),
            "entry_quality": str(entry_plan.get("entry_quality", "NONE") or "NONE"),
            "actionable": execution_action in {"BUY", "SELL"},
            "dominant_side": str(decision_kernel.get("dominant_side", "hold") or "hold").upper(),
            "setup_state": str(decision_kernel.get("state", "IDLE") or "IDLE"),
            "decision": str(decision_kernel.get("decision", "STAND_ASIDE") or "STAND_ASIDE"),
            "decision_kernel": decision_kernel,
            "global_local_control": control_state,
            "control_owner": str(control_state.get("owner", "balanced")),
            "control_direction": str(control_state.get("direction", "HOLD")),
            "control_horizon_candles": int(control_state.get("estimated_control_candles", 0) or 0),
            "countertrend_lane": countertrend_lane,
            "entry_distance": entry_distance,
            "support_resistance_zones": support_resistance_zones,
            "timing_signal": {
                "entry_state": str(entry_plan.get("timing_state", "WATCH") or "WATCH"),
                "timing_score": _clip01(entry_plan.get("timing_score", 0.0)),
                "entry_quality": str(entry_plan.get("entry_quality", "NONE") or "NONE"),
                "instruction": str(entry_plan.get("instruction", "") or ""),
                "timeframe": timeframe,
            },
            "overlay_instructions": [
                "SNIPER WATCH is an early zone; wait for rejection/reclaim in the projected direction.",
                "TRIGGER READY is confirmation; it is safer but normally later.",
                "INVALIDATED cancels the setup. Do not buy or sell because price reached invalidate.",
            ],
            "probability": {
                "target_first_probability": probability_payload["target_first_probability"],
                "invalidation_first_probability": probability_payload["invalidation_first_probability"],
                "sideways_probability": probability_payload["sideways_probability"],
                "expected_candles_to_resolution": probability_payload["expected_candles_to_resolution"],
                "sample_weight": probability_payload["sample_weight"],
                "probability_state": probability_payload["probability_state"],
            },
            "behavior": {
                "current_state": str(behavior_payload.get("current_state", "noise") or "noise"),
                "next_most_likely_state": str(behavior_payload.get("next_most_likely_state", "sideways_pause") or "sideways_pause"),
                "state_confidence": _clip01(behavior_payload.get("state_confidence", 0.0)),
                "trend_phase": str(behavior_payload.get("trend_phase", "unknown") or "unknown"),
                "move_quality": str(behavior_payload.get("move_quality", "unknown") or "unknown"),
            },
            "reasons": reasons,
            "timestamp": _now_iso(),
        }
        execution_lane = "COUNTERTREND_SCALP" if bool(countertrend_lane.get("actionable", False)) else "PRIMARY"
        dynamic_expiry_seconds = int(self._execution_expiry_seconds(signal, tracking_summary, lane=execution_lane))
        signal["execution_lane"] = execution_lane
        signal["expiry_seconds"] = dynamic_expiry_seconds
        signal["expiry_text"] = PocketOptionBrokerExecutionBackend._format_expiry_text(dynamic_expiry_seconds)  # pyright: ignore[reportPrivateUsage]
        signal["expiry_source"] = "decision_kernel_dynamic"
        map_timing = self._execution_map_timing_payload(
            entry_plan,
            decision_kernel,
            timeframe=timeframe,
            expiry_seconds=dynamic_expiry_seconds,
            lane=execution_lane,
        )
        tracking_summary["map_timing"] = map_timing
        signal["map_timing"] = map_timing
        signal["candles_remaining_in_sniper_zone"] = int(map_timing.get("candles_remaining_in_sniper_zone", 0) or 0)
        signal["candles_to_target"] = int(map_timing.get("candles_to_target_from_now", 0) or 0)
        signal["target_reached"] = bool(map_timing.get("target_reached", False))
        if bool(map_timing.get("target_reached", False)):
            signal["execution_action"] = "HOLD"
            signal["actionable"] = False
            signal["execution_permission"] = "WAIT"
            signal["status"] = "target_complete"

        phoenixguard_report = self._build_phoenixguard_report(chart_image, candles, tracking_summary, signal)
        tracking_summary["phoenixguard_report"] = phoenixguard_report
        signal["phoenixguard_decision_state"] = str(phoenixguard_report.get("decision_state", "forming") or "forming")
        signal["phoenixguard_report_summary"] = str(phoenixguard_report.get("headline", signal.get("summary", "")) or signal.get("summary", ""))
        signal["phoenixguard_report_status"] = str(phoenixguard_report.get("status", "warming") or "warming")
        return tracking_summary, signal

    def _build_global_local_control_payload(
        self,
        *,
        global_direction: str,
        local_direction: str,
        impulse_direction: str,
        global_slope: float,
        local_slope: float,
        current_slope: float,
        impulse_delta: float,
        latest_body_height_pct: float,
        consolidation_score: float,
        continuation_score: float,
        reversal_score: float,
        candle_statistics: Mapping[str, Any],
        decision_kernel: Mapping[str, Any],
    ) -> dict[str, Any]:
        global_side = _upper_action(global_direction)
        local_side = _upper_action(local_direction)
        impulse_side = _upper_action(impulse_direction)
        global_strength = _clip01(abs(float(global_slope)) * 5.2 + continuation_score * 0.30)
        local_strength = _clip01(abs(float(local_slope)) * 5.8 + latest_body_height_pct * 1.12)
        current_strength = _clip01(abs(float(current_slope)) * 5.4 + abs(float(impulse_delta)) * 6.0 + latest_body_height_pct * 0.82)
        momentum_consistency = _clip01(candle_statistics.get("momentum_consistency", 0.0))
        direction_run = int(candle_statistics.get("direction_run", 0) or 0)
        kernel_side = _upper_action(decision_kernel.get("dominant_side", "HOLD"))
        kernel_confidence = _clip01(decision_kernel.get("confidence", decision_kernel.get("score", 0.0)))
        alignment = bool(global_side in {"BUY", "SELL"} and global_side == local_side)
        local_takeover = bool(
            local_side in {"BUY", "SELL"}
            and local_side != global_side
            and (local_strength + current_strength * 0.44) >= (global_strength + 0.14)
        )
        if alignment and not local_takeover:
            owner = "global"
            direction = global_side
            control_strength = _clip01(0.52 * global_strength + 0.28 * local_strength + 0.20 * momentum_consistency)
        elif local_takeover:
            owner = "local"
            direction = local_side
            control_strength = _clip01(0.48 * local_strength + 0.30 * current_strength + 0.22 * reversal_score)
        elif impulse_side in {"BUY", "SELL"} and current_strength >= max(global_strength, local_strength) + 0.08:
            owner = "current"
            direction = impulse_side
            control_strength = _clip01(0.58 * current_strength + 0.22 * local_strength + 0.20 * latest_body_height_pct)
        elif global_side in {"BUY", "SELL"}:
            owner = "global"
            direction = global_side
            control_strength = global_strength
        elif local_side in {"BUY", "SELL"}:
            owner = "local"
            direction = local_side
            control_strength = local_strength
        else:
            owner = "balanced"
            direction = kernel_side if kernel_side in {"BUY", "SELL"} else "HOLD"
            control_strength = _clip01(kernel_confidence * 0.70)

        if kernel_side in {"BUY", "SELL"} and direction in {"BUY", "SELL"} and kernel_side != direction:
            control_strength = _clip01(control_strength * 0.74)
        estimated_candles = int(
            np.clip(
                round(
                    1.0
                    + 5.0 * control_strength
                    + 0.35 * max(0, direction_run)
                    + 1.4 * float(alignment)
                    - 2.2 * reversal_score
                    - 1.2 * consolidation_score
                ),
                1,
                14,
            )
        )
        return {
            "owner": owner,
            "direction": direction,
            "estimated_control_candles": estimated_candles,
            "global_direction": global_side,
            "local_direction": local_side,
            "impulse_direction": impulse_side,
            "global_strength": global_strength,
            "local_strength": local_strength,
            "current_strength": current_strength,
            "control_strength": control_strength,
            "alignment": alignment,
            "local_takeover": local_takeover,
            "direction_run": direction_run,
            "kernel_side": kernel_side,
        }

    def _execution_map_timing_payload(
        self,
        entry_plan: Mapping[str, Any],
        decision_kernel: Mapping[str, Any],
        *,
        timeframe: str,
        expiry_seconds: int,
        lane: str,
    ) -> dict[str, Any]:
        entry_map = _mapping_to_dict(entry_plan.get("map_timing", {}))
        tf_seconds = _timeframe_seconds(timeframe, default=300)
        entry_state = str(entry_plan.get("entry_state", "WAIT") or "WAIT").upper()
        eta_trigger = max(0, int(decision_kernel.get("eta_trigger_candles", entry_map.get("candles_to_trigger_zone", 0)) or 0))
        eta_target_after_trigger = max(
            0,
            int(decision_kernel.get("eta_target_after_trigger_candles", entry_map.get("candles_to_target_zone", 0)) or 0),
        )
        eta_invalidation = max(0, int(decision_kernel.get("eta_invalidation_candles", 0) or 0))
        if entry_state in {"SNIPER_READY", "TRIGGER_READY", "TRIGGERED", "ACTIVE"}:
            candles_to_target_from_now = eta_target_after_trigger
        else:
            candles_to_target_from_now = eta_trigger + eta_target_after_trigger
        expiry_candles = int(np.ceil(max(1, int(expiry_seconds or 0)) / max(1, tf_seconds)))
        target_reached = bool(entry_plan.get("target_reached", entry_map.get("target_reached", False)))
        return {
            **entry_map,
            "entry_state": entry_state,
            "timeframe": str(timeframe or "M5").upper(),
            "timeframe_seconds": int(tf_seconds),
            "lane": str(lane or "PRIMARY").upper(),
            "candles_to_trigger": int(eta_trigger),
            "seconds_to_trigger": _candles_to_seconds(eta_trigger, timeframe),
            "candles_to_target_after_trigger": int(eta_target_after_trigger),
            "seconds_to_target_after_trigger": _candles_to_seconds(eta_target_after_trigger, timeframe),
            "candles_to_target_from_now": int(candles_to_target_from_now),
            "seconds_to_target_from_now": _candles_to_seconds(candles_to_target_from_now, timeframe),
            "candles_to_invalidation": int(eta_invalidation),
            "seconds_to_invalidation": _candles_to_seconds(eta_invalidation, timeframe),
            "execution_duration_candles": int(expiry_candles),
            "execution_duration_seconds": int(max(1, expiry_seconds)),
            "expiry_seconds": int(max(1, expiry_seconds)),
            "target_reached": target_reached,
            "target_complete_gate": bool(target_reached),
            "trigger_allowed": bool(not target_reached and entry_state not in {"COMPLETE", "INVALIDATED"}),
            "monitor_mode": str(entry_plan.get("post_target_monitor", entry_map.get("post_target_monitor", "monitor_short_pullback"))),
            "message": (
                "Target is already complete; monitor continuation or pullback before a new trigger."
                if target_reached
                else (
                    f"Sniper window has {int(entry_map.get('candles_remaining_in_sniper_zone', 0) or 0)} candle(s) left; "
                    f"target ETA is {int(candles_to_target_from_now)} candle(s)."
            )
        ),
    }

    def _build_decision_kernel_snapshot(
        self,
        *,
        candles: Sequence[Mapping[str, Any]],
        timeframe: str,
        setup: str,
        projection: Mapping[str, Any],
        entry_plan: Mapping[str, Any],
        probability_payload: Mapping[str, Any],
        behavior_payload: Mapping[str, Any],
        candle_statistics: Mapping[str, Any],
        candidate_action: str,
        execution_action: str,
        global_direction: str,
        local_direction: str,
        impulse_direction: str,
        global_slope: float,
        local_slope: float,
        current_slope: float,
        impulse_delta: float,
        confidence: float,
        consolidation_score: float,
        continuation_score: float,
        impulse_score: float,
        reversal_score: float,
        latest_body_height_pct: float,
        session_payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        direction = str(projection.get("direction", candidate_action) or candidate_action).upper()
        if direction not in {"BUY", "SELL"}:
            direction = str(candidate_action or "HOLD").upper()
        tokens = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], behavior_payload.get("candle_tokens", []))
            if isinstance(item, Mapping)
        ]
        latest_token = dict(tokens[-1]) if tokens else {}
        box_context = _mapping_to_dict(behavior_payload.get("box_context", {}))
        direction_run = int(candle_statistics.get("direction_run", 0) or 0)
        sample_size = int(candle_statistics.get("sample_size", len(candles)) or len(candles))
        latest_direction = str(candles[-1].get("direction", "HOLD") or "HOLD").upper() if candles else "HOLD"
        entry_state = str(entry_plan.get("entry_state", "WAIT") or "WAIT").upper()

        def token_side(token: Mapping[str, Any]) -> str:
            value = str(token.get("direction", "HOLD") or "HOLD").upper()
            return value if value in {"BUY", "SELL"} else "HOLD"

        def candles_since_event(patterns: Sequence[str], side: str = "") -> int:
            wanted = str(side or "").upper()
            lowered = tuple(str(pattern).lower() for pattern in patterns if str(pattern).strip())
            for offset, token in enumerate(reversed(tokens)):
                if wanted in {"BUY", "SELL"} and token_side(token) != wanted:
                    continue
                event = str(token.get("micro_structure_event", "") or "").lower()
                if any(pattern in event for pattern in lowered):
                    return int(offset)
            return int(len(tokens))

        strengthening_age = candles_since_event(("rejection", "impulse", "continuation"), direction)
        rejection_age = candles_since_event(("rejection",), direction)
        trigger_touch_age = candles_since_event(("trigger", "breakout"), direction)
        zone_touch_age = 0
        for offset, token in enumerate(reversed(tokens)):
            if token_side(token) != direction:
                continue
            if _clip01(token.get("distance_to_trigger", 1.0)) <= 0.08 or _clip01(token.get("distance_to_sniper", 1.0)) <= 0.12:
                zone_touch_age = int(offset)
                break
        else:
            zone_touch_age = int(len(tokens))

        if direction in {"BUY", "SELL"} and latest_direction == direction and direction_run > 0:
            setup_age = min(sample_size, direction_run)
        elif direction in {"BUY", "SELL"}:
            setup_age = min(sample_size, max(1, int(round(sample_size * 0.45))))
        else:
            setup_age = 0
        if direction in {"BUY", "SELL"}:
            fresh_reaction_age = min(strengthening_age, rejection_age, trigger_touch_age, zone_touch_age)
            if fresh_reaction_age < len(tokens) and (
                _clip01(latest_token.get("distance_to_trigger", 1.0)) <= 0.30
                or _clip01(latest_token.get("distance_to_sniper", 1.0)) <= 0.18
                or int(box_context.get("acceptance_count", 0) or 0) > 0
            ):
                setup_age = min(setup_age, max(1, int(fresh_reaction_age) + 1))
            if entry_state in {"SNIPER_WATCH", "WAIT_FOR_TRIGGER", "WAIT_FOR_SNIPER"} and _clip01(latest_token.get("distance_to_trigger", 1.0)) <= 0.20:
                setup_age = min(setup_age, 2)
            if entry_state in {"SNIPER_READY", "TRIGGER_READY"}:
                setup_age = min(setup_age, 1)

        setup_family = setup.upper()
        max_valid_age = 8
        if "CONSOLIDATION" in setup_family:
            max_valid_age = 9
        elif "REVERSAL" in setup_family:
            max_valid_age = 7
        elif "IMPULSE" in setup_family:
            max_valid_age = 6

        distance_to_trigger = _clip01(latest_token.get("distance_to_trigger", 1.0))
        distance_to_target = _clip01(latest_token.get("distance_to_target", 1.0))
        distance_to_invalidation = _clip01(latest_token.get("distance_to_invalidation", 1.0))
        momentum_consistency = _clip01(candle_statistics.get("momentum_consistency", 0.0))
        average_step = _float_or(candle_statistics.get("average_step", 0.0), 0.0)
        directional_speed = max(0.006, average_step * (0.55 + momentum_consistency) + abs(float(impulse_delta)) * 0.38)
        candidate_ratio = _clip01(candle_statistics.get("candidate_ratio", 0.0))
        opposing_ratio = _clip01(candle_statistics.get("opposing_ratio", 0.0))
        state_confidence = _clip01(behavior_payload.get("state_confidence", 0.0))
        failure_risk = _clip01(box_context.get("failure_risk", 0.0))
        timing_score = _clip01(entry_plan.get("timing_score", 0.0))

        signals: list[dict[str, Any]] = []

        def add_signal(
            side: str,
            confidence_value: float,
            quality: float,
            zone_type: str,
            *,
            weight: float = 1.0,
            age: int | None = None,
            distance: float | None = None,
            persistence: float | None = None,
        ) -> None:
            normalized_side = str(side or "HOLD").upper()
            if normalized_side not in {"BUY", "SELL"}:
                return
            signals.append(
                {
                    "side": normalized_side,
                    "confidence": _clip01(confidence_value),
                    "quality": _clip01(quality),
                    "zone_type": str(zone_type or "signal"),
                    "weight_model": float(weight),
                    "age_candles": int(setup_age if age is None else max(0, age)),
                    "distance_to_trigger": distance_to_trigger if distance is None else _clip01(distance),
                    "distance_to_target": distance_to_target,
                    "persistence_factor": _clip01(0.45 + 0.55 * momentum_consistency if persistence is None else persistence),
                }
            )

        add_signal(
            global_direction,
            _clip01(0.34 + abs(float(global_slope)) * 4.2),
            _clip01(0.56 + continuation_score * 0.28),
            "global",
            weight=0.88,
            age=max(0, setup_age - 2),
            persistence=0.62,
        )
        add_signal(
            local_direction,
            _clip01(0.38 + abs(float(local_slope)) * 4.8),
            _clip01(0.58 + max(continuation_score, reversal_score) * 0.30),
            "local",
            weight=1.04,
            age=max(0, setup_age - 1),
        )
        add_signal(
            impulse_direction,
            _clip01(0.30 + impulse_score * 0.68),
            _clip01(0.52 + latest_body_height_pct * 2.0),
            "current",
            weight=1.14,
            age=0,
            persistence=_clip01(0.48 + min(0.46, float(direction_run) * 0.08)),
        )
        add_signal(
            direction,
            _clip01(projection.get("confidence", confidence)),
            _clip01(0.54 + timing_score * 0.28 + (1.0 - failure_risk) * 0.16),
            "trigger",
            weight=1.18,
            age=setup_age,
            distance=distance_to_trigger,
        )
        add_signal(
            direction,
            _clip01(0.30 + candidate_ratio * 0.70),
            _clip01(0.46 + momentum_consistency * 0.42),
            "candle_count",
            weight=1.00,
            age=setup_age,
            persistence=_clip01(0.46 + min(0.44, float(direction_run) * 0.07)),
        )
        add_signal(
            direction,
            state_confidence,
            _clip01(0.50 + (1.0 - failure_risk) * 0.38),
            "behavior",
            weight=1.08,
            age=setup_age,
            persistence=_clip01(0.52 + state_confidence * 0.34),
        )
        if direction in {"BUY", "SELL"} and opposing_ratio >= 0.22:
            add_signal(
                "SELL" if direction == "BUY" else "BUY",
                _clip01(0.18 + opposing_ratio * 0.66 + failure_risk * 0.18),
                _clip01(0.42 + reversal_score * 0.42),
                "opposition",
                weight=0.92,
                age=0,
                persistence=_clip01(0.42 + opposing_ratio * 0.45),
            )

        session = _mapping_to_dict(session_payload or {})
        memory_rows = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], session.get("recent_studies", []))
            if isinstance(item, Mapping)
        ]

        return {
            "pair": str(session.get("market", "") or ""),
            "timeframe": str(timeframe or "").upper(),
            "signals": signals,
            "distances": {
                "trigger": distance_to_trigger,
                "target": distance_to_target,
                "invalidation": distance_to_invalidation,
            },
            "directional_speed": directional_speed,
            "candle_statistics": dict(candle_statistics),
            "behavior": dict(behavior_payload),
            "box_context": box_context,
            "probability": dict(probability_payload),
            "memory_rows": memory_rows,
            "context": {
                "pair": str(session.get("market", "") or ""),
                "timeframe": str(timeframe or "").upper(),
                "setup": setup,
                "setup_age_candles": int(setup_age),
                "ttl_candles": int(max_valid_age),
                "visible_candle_count": int(sample_size),
                "candidate_action": str(candidate_action or "HOLD").upper(),
                "execution_action": str(execution_action or "HOLD").upper(),
                "entry_state": entry_state,
                "target_reached": bool(entry_plan.get("target_reached", False)),
                "map_timing": dict(_mapping_to_dict(entry_plan.get("map_timing", {}))),
                "candles_until_sniper_zone": int(entry_plan.get("candles_until_sniper_zone", 0) or 0),
                "candles_remaining_in_sniper_zone": int(entry_plan.get("candles_remaining_in_sniper_zone", 0) or 0),
                "candles_to_target_zone": int(entry_plan.get("candles_to_target_zone", 0) or 0),
                "global_direction": str(global_direction or "HOLD").upper(),
                "local_direction": str(local_direction or "HOLD").upper(),
                "current_direction": str(impulse_direction or "HOLD").upper(),
                "impulse_direction": str(impulse_direction or "HOLD").upper(),
                "latest_direction": latest_direction,
                "distance_to_trigger": distance_to_trigger,
                "distance_to_target": distance_to_target,
                "distance_to_invalidation": distance_to_invalidation,
                "directional_speed": directional_speed,
                "congestion_score": consolidation_score,
                "continuation_score": continuation_score,
                "impulse_score": impulse_score,
                "reversal_score": reversal_score,
                "failure_risk": failure_risk,
                "opposing_ratio": opposing_ratio,
                "persistence": momentum_consistency,
                "timing_score": timing_score,
                "impulse_delta": float(impulse_delta),
                "latest_token": latest_token,
                "candle_tokens": tokens[-36:],
                "clocks": {
                    "candles_since_strengthening": int(strengthening_age),
                    "candles_since_rejection": int(rejection_age),
                    "candles_since_trigger_touch": int(trigger_touch_age),
                    "candles_since_zone_touch": int(zone_touch_age),
                },
                "probability": dict(probability_payload),
                "candle_statistics": dict(candle_statistics),
                "behavior": dict(behavior_payload),
                "box_context": box_context,
                "memory_rows": memory_rows,
            },
        }

    def _scenario_chart_state_for_tracker(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> dict[str, Any]:
        tracked_candles = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], tracking_summary.get("tracked_candles", []))
            if isinstance(item, Mapping)
        ]
        if not tracked_candles:
            return {}

        action = _upper_action(latest_signal.get("action", "HOLD"))
        confidence = _clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", 0.5)))

        def _read_ohlc(candle: Mapping[str, Any], key_o: str, key_h: str, key_l: str, key_c: str, fallback: float) -> tuple[float, float, float, float]:
            o = _float_or(candle.get(key_o, candle.get("open", fallback)), fallback)
            h = _float_or(candle.get(key_h, candle.get("high", fallback)), fallback)
            l = _float_or(candle.get(key_l, candle.get("low", fallback)), fallback)
            c = _float_or(candle.get(key_c, candle.get("close", fallback)), fallback)
            return o, h, l, c

        entry = tracked_candles[-1]
        fallback = _float_or(tracking_summary.get("latest_price_proxy", 1.0), 1.0)
        o, h, l, c = _read_ohlc(entry, "o", "h", "l", "c", fallback)

        recent_candles: list[dict[str, Any]] = []
        for row in tracked_candles[-20:]:
            ro, rh, rl, rc = _read_ohlc(row, "o", "h", "l", "c", c)
            recent_candles.append(
                {
                    "o": ro,
                    "h": max(ro, rh, rc),
                    "l": min(ro, rl, rc),
                    "c": rc,
                    "v": 1.0,
                    "dir": _upper_action(row.get("direction", action)),
                    "conf": _clip01(row.get("confidence", confidence)),
                }
            )

        return {
            "entry_candle": {
                "o": o,
                "h": max(o, h, c),
                "l": min(o, l, c),
                "c": c,
                "v": 1.0,
            },
            "recent_candles": recent_candles,
            "direction": action,
            "direction_probability": confidence,
        }

    def _scenario_forecast_for_tracker(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> dict[str, Any]:
        tracked_candles = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], tracking_summary.get("tracked_candles", []))
            if isinstance(item, Mapping)
        ]
        if tracked_candles:
            closes = np.array([
                _float_or(row.get("c", row.get("close", tracking_summary.get("latest_price_proxy", 1.0))), 1.0)
                for row in tracked_candles[-24:]
            ], dtype=np.float32)
            highs = np.array([
                _float_or(row.get("h", row.get("high", closes[-1])), float(closes[-1]))
                for row in tracked_candles[-24:]
            ], dtype=np.float32)
            lows = np.array([
                _float_or(row.get("l", row.get("low", closes[-1])), float(closes[-1]))
                for row in tracked_candles[-24:]
            ], dtype=np.float32)
        else:
            close_value = _float_or(tracking_summary.get("latest_price_proxy", 1.0), 1.0)
            closes = np.array([close_value], dtype=np.float32)
            highs = np.array([close_value], dtype=np.float32)
            lows = np.array([close_value], dtype=np.float32)

        last_close = float(closes[-1])
        local_slope = _float_or(tracking_summary.get("local_slope", 0.0), 0.0)
        confidence = _clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", 0.5)))

        range_proxy = max(
            0.0005,
            float(np.max(highs) - np.min(lows)),
            float(np.std(closes)) * 2.0,
            abs(local_slope) * 2.5,
        )
        q50 = float(last_close + local_slope * 0.5)
        q05 = float(q50 - range_proxy * 0.35)
        q95 = float(q50 + range_proxy * 0.35)

        continuation = _clip01(tracking_summary.get("continuation_score", 0.5))
        pullback = _clip01(0.18 + (1.0 - continuation) * 0.32)
        reversal = _clip01(tracking_summary.get("reversal_score", 0.12))
        fakeout = _clip01(max(0.05, 1.0 - continuation - pullback - reversal))

        total_prob = max(1e-6, continuation + pullback + reversal + fakeout)
        continue_prob = continuation / total_prob
        pullback_prob = pullback / total_prob
        reversal_prob = reversal / total_prob
        fakeout_prob = fakeout / total_prob

        return {
            "q05": q05,
            "q50": q50,
            "q95": q95,
            "poly_slope": local_slope,
            "path_confidence": confidence,
            "continue_prob": continue_prob,
            "pullback_prob": pullback_prob,
            "reversal_attempt_prob": reversal_prob,
            "fakeout_prob": fakeout_prob,
            "structure_trade_ready": _clip01(1.0 if latest_signal.get("actionable", False) and confidence > 0.55 else 0.0),
            "interval": max(0.0001, range_proxy * 0.08),
        }

    def _build_tracker_scenario_analysis(
        self,
        *,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        session_payload: Mapping[str, Any],
        controls: Mapping[str, Any],
    ) -> dict[str, Any]:
        enabled = bool(controls.get("scenario_generation_enabled", False))
        base: dict[str, Any] = {
            "enabled": enabled,
            "status": "disabled" if not enabled else "idle",
            "summary": "A* scenario generation is disabled.",
            "generated_at": _now_iso(),
            "total_scenarios": 0,
            "top_scenario": {},
            "scenarios": [],
            "overlay": {"confidence_heatmap": [], "tree_structure": {}},
        }
        if not enabled:
            return base

        chart_state = self._scenario_chart_state_for_tracker(tracking_summary, latest_signal)
        if not chart_state:
            base["status"] = "insufficient_data"
            base["summary"] = "Scenario generation skipped: no tracked candles are available yet."
            return base

        forecast_output = self._scenario_forecast_for_tracker(tracking_summary, latest_signal)
        dominant_memory_side = _upper_action(
            _mapping_to_dict(session_payload.get("execution_memory_projection", {})).get("dominant_side", "HOLD")
        )
        memory_recall = {
            "memory_alignment": _clip01(
                _mapping_to_dict(session_payload.get("execution_memory_projection", {})).get(
                    "memory_similarity",
                    latest_signal.get("memory_similarity", tracking_summary.get("memory_similarity", 0.5)),
                )
            ),
            "memory_labels": [dominant_memory_side] if dominant_memory_side in {"BUY", "SELL"} else [],
        }

        try:
            scenarios = predict_scenarios_from_chart_and_forecast(
                chart_state=chart_state,
                forecast_output=forecast_output,
                memory_recall=memory_recall,
                num_scenarios=5,
                max_depth=5,
            )
            ranked = rank_scenarios_by_ensemble_agreement(
                scenarios,
                ensemble_decision=_upper_action(latest_signal.get("action", "HOLD")),
                ensemble_confidence=_clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", 0.5))),
            )
            paint = scenarios_to_paint_layer(ranked, chart_state)
        except Exception as exc:
            LOGGER.exception("Scenario generation failed during tracker capture.")
            base["status"] = "error"
            base["summary"] = f"Scenario generation failed: {exc}"
            return base

        if not ranked:
            base["status"] = "insufficient_data"
            base["summary"] = "Scenario generation completed but returned no viable paths."
            return base

        top = ranked[0]
        top_last = top.scenario.last_candle()
        compact: list[dict[str, Any]] = []
        for scenario in ranked[:3]:
            compact.append(
                {
                    "rank": int(scenario.rank),
                    "direction": str(scenario.scenario.last_candle().direction),
                    "probability": float(scenario.probability),
                    "cost": float(scenario.scenario.cost),
                    "transition_type": str(scenario.scenario.transition_type.value),
                    "memory_alignment": float(scenario.scenario.memory_alignment),
                }
            )

        heatmap = paint.get("confidence_heatmap", [])
        tree = _mapping_to_dict(paint.get("tree_structure", {}))
        return {
            "enabled": True,
            "status": "ready",
            "summary": str(paint.get("summary", "Scenario generation ready.")),
            "generated_at": _now_iso(),
            "total_scenarios": len(ranked),
            "top_scenario": {
                "rank": int(top.rank),
                "direction": str(top_last.direction),
                "probability": float(top.probability),
                "cost": float(top.scenario.cost),
                "transition_type": str(top.scenario.transition_type.value),
                "memory_alignment": float(top.scenario.memory_alignment),
            },
            "scenarios": compact,
            "overlay": {
                "confidence_heatmap": heatmap,
                "tree_structure": tree,
                "heatmap_shape": [len(heatmap), int(tree.get("branches", 0) or 0)],
            },
        }

    def _structure_box(
        self,
        candles: Sequence[Mapping[str, Any]],
        image_size: tuple[int, int],
        key: str,
        label: str,
        *,
        direction: str = "HOLD",
    ) -> dict[str, Any]:
        if not candles:
            return {"key": key, "label": label, "bbox": [0, 0, 1, 1]}
        x0 = min(int(candle["bbox"][0]) for candle in candles)
        y0 = min(int(candle["bbox"][1]) for candle in candles)
        x1 = max(int(candle["bbox"][2]) for candle in candles)
        y1 = max(int(candle["bbox"][3]) for candle in candles)
        bbox = _expand_bbox(image_size, [x0, y0, x1, y1], pad_x=10, pad_y=10)
        payload = {
            "key": key,
            "label": label,
            "bbox": bbox,
            "meta": _pixel_bbox_meta(image_size, bbox),
            "direction": _upper_action(direction),
            "candle_count": int(len(candles)),
        }
        payload.update(self._structure_box_micro_plan(bbox, _upper_action(direction), image_size, candle_count=len(candles)))
        return payload

    def _structure_box_micro_plan(
        self,
        bbox: Sequence[Any],
        direction: str,
        image_size: tuple[int, int],
        *,
        candle_count: int = 0,
    ) -> dict[str, Any]:
        normalized = _upper_action(direction)
        if normalized not in {"BUY", "SELL"} or len(bbox) < 4:
            return {}
        image_w, image_h = int(image_size[0]), int(image_size[1])
        x0, y0, x1, y1 = [float(value) for value in bbox[:4]]
        width = max(1.0, x1 - x0)
        height = max(1.0, y1 - y0)
        sign_y = -1.0 if normalized == "BUY" else 1.0
        center_y = 0.5 * (y0 + y1)
        plan_x0 = float(np.clip(x0 + width * 0.10, 0.0, max(0.0, image_w - 2.0)))
        plan_x1 = float(np.clip(x1 - width * 0.08, plan_x0 + 8.0, max(plan_x0 + 8.0, image_w - 1.0)))
        band = float(np.clip(height * 0.16, 4.0, max(5.0, image_h * 0.028)))

        def band_box(center: float) -> list[float]:
            top = float(np.clip(center - band * 0.5, 0.0, max(0.0, image_h - 2.0)))
            bottom = float(np.clip(center + band * 0.5, top + 3.0, max(top + 3.0, image_h - 1.0)))
            return [plan_x0, top, plan_x1, bottom]

        sniper_y = center_y - sign_y * height * 0.34
        trigger_y = center_y + sign_y * height * 0.08
        target_y = center_y + sign_y * height * 0.48
        invalidation_y = float(np.clip(center_y - sign_y * height * 0.58, 0.0, max(0.0, image_h - 1.0)))
        control_hold = int(np.clip(max(1, candle_count) + 2, 1, 14))
        return {
            "sniper_window": band_box(sniper_y),
            "trigger_window": band_box(trigger_y),
            "target_window": band_box(target_y),
            "invalidation_y": invalidation_y,
            "sniper_target_plan": {
                "direction": normalized,
                "sniper": band_box(sniper_y),
                "trigger": band_box(trigger_y),
                "target": band_box(target_y),
                "invalidation_y": invalidation_y,
                "control_hold_candles": control_hold,
            },
            "control_hold_candles": control_hold,
        }

    def _build_historical_structure(
        self,
        candles: Sequence[Mapping[str, Any]],
        image_size: tuple[int, int],
    ) -> list[dict[str, Any]]:
        rows = [dict(item) for item in candles]
        if len(rows) < 6:
            return []
        rows.sort(key=lambda item: float(item.get("center_x", 0.0) or 0.0))
        max_segments = 7
        segment_count = min(max_segments, max(2, int(round(len(rows) / 5.0))))
        chunk_size = max(3, int(np.ceil(len(rows) / max(1, segment_count))))
        segments: list[dict[str, Any]] = []
        previous_direction = "HOLD"
        for segment_index, start in enumerate(range(0, len(rows), chunk_size), start=1):
            segment = rows[start:min(len(rows), start + chunk_size)]
            if len(segment) < 2:
                continue
            proxies = [float(item.get("price_proxy", 0.0) or 0.0) for item in segment]
            slope = _regression_slope(proxies)
            direction = _trend_direction(slope, epsilon=0.012)
            if direction == "HOLD":
                direction = str(segment[-1].get("direction", previous_direction) or previous_direction).upper()
                if direction not in {"BUY", "SELL"}:
                    direction = "HOLD"
            previous_direction = direction if direction in {"BUY", "SELL"} else previous_direction
            box = self._structure_box(
                segment,
                image_size,
                f"history_{segment_index}",
                f"HISTORY {segment_index}",
                direction=direction,
            )
            start_point = [
                int(round(float(segment[0].get("center_x", 0.0) or 0.0))),
                int(round(float(segment[0].get("center_y", 0.0) or 0.0))),
            ]
            end_point = [
                int(round(float(segment[-1].get("center_x", 0.0) or 0.0))),
                int(round(float(segment[-1].get("center_y", 0.0) or 0.0))),
            ]
            net_move = float(proxies[-1] - proxies[0]) if len(proxies) >= 2 else 0.0
            segments.append(
                {
                    **box,
                    "direction": direction,
                    "label": f"H{segment_index} {direction}",
                    "sequence_index": int(segment_index),
                    "candle_count": int(len(segment)),
                    "slope": float(slope),
                    "net_move": net_move,
                    "start_point": start_point,
                    "end_point": end_point,
                    "story": self._historical_segment_story(direction, net_move, len(segment), segment_index),
                }
            )
        return segments

    def _historical_segment_story(self, direction: str, net_move: float, candle_count: int, segment_index: int) -> str:
        normalized = str(direction or "HOLD").upper()
        if normalized == "BUY":
            return f"History leg {segment_index}: buyers lifted price across {candle_count} candles."
        if normalized == "SELL":
            return f"History leg {segment_index}: sellers pressed price lower across {candle_count} candles."
        if abs(float(net_move)) <= 0.012:
            return f"History leg {segment_index}: price paused and built range memory."
        return f"History leg {segment_index}: mixed transition before the current setup."

    def _build_projection_payload(
        self,
        candles: Sequence[Mapping[str, Any]],
        image_size: tuple[int, int],
        *,
        candidate_action: str,
        execution_action: str,
        global_direction: str,
        local_direction: str,
        impulse_direction: str,
        confidence: float,
        local_slope: float,
        impulse_delta: float,
        recent_range: float,
        latest_body_height_pct: float,
    ) -> dict[str, Any]:
        if len(candles) < 5:
            return {}
        width = max(1, int(image_size[0]))
        height = max(1, int(image_size[1]))
        latest = candles[-1]
        latest_x = float(latest.get("center_x", width * 0.82) or width * 0.82)
        latest_y = float(latest.get("center_y", height * 0.50) or height * 0.50)
        centers = [float(item.get("center_x", 0.0) or 0.0) for item in candles if float(item.get("center_x", 0.0) or 0.0) > 0.0]
        x_steps = [right - left for left, right in zip(centers, centers[1:]) if (right - left) > 1.0]
        step_x = float(np.median(np.asarray(x_steps, dtype=np.float32))) if x_steps else max(14.0, width * 0.035)

        recent = candles[-min(8, len(candles)):]
        all_tops = [float(item.get("bbox", [0, latest_y, 0, latest_y])[1]) for item in candles]
        all_bottoms = [float(item.get("bbox", [0, latest_y, 0, latest_y])[3]) for item in candles]
        recent_top = min(float(item.get("bbox", [0, latest_y, 0, latest_y])[1]) for item in recent)
        recent_bottom = max(float(item.get("bbox", [0, latest_y, 0, latest_y])[3]) for item in recent)
        band_height = max(12, min(30, int(round(height * (0.022 + _clip01(recent_range) * 0.034)))))
        price_top = int(round(max(height * 0.045, min(all_tops) - band_height * 2.0)))
        price_bottom = int(round(min(height * 0.975, max(all_bottoms) + band_height * 2.0)))
        if price_bottom <= price_top + band_height * 5:
            midpoint = int(round(latest_y))
            price_top = max(4, midpoint - band_height * 4)
            price_bottom = min(height - 4, midpoint + band_height * 4)
        fit_top = max(4, price_top)
        fit_bottom = min(height - 4, price_bottom)
        vertical_min = fit_top + band_height + 2
        vertical_max = fit_bottom - band_height - 2
        if vertical_max <= vertical_min:
            vertical_min = band_height + 4
            vertical_max = height - band_height - 4

        right_limit = max(8, width - max(10, int(round(width * 0.018))))
        zone_width = int(round(max(30.0, min(float(width) * 0.12, step_x * 2.18))))
        future_gap = max(4.0, step_x * 0.18)
        preferred_future_x0 = latest_x + future_gap
        if preferred_future_x0 + zone_width <= right_limit:
            future_x0 = int(round(preferred_future_x0))
        else:
            fitted_x0 = right_limit - zone_width
            future_x0 = int(round(max(4.0, min(fitted_x0, latest_x + step_x * 0.18))))
        future_x1 = int(round(min(float(right_limit), future_x0 + zone_width)))
        if future_x1 <= future_x0 + 12:
            future_x0 = max(4, int(round(right_limit - max(16.0, step_x * 2.6))))
            future_x1 = int(round(right_limit))

        move_strength = _clip01(
            0.36 * confidence
            + 0.28 * _clip01(abs(local_slope) * 3.4)
            + 0.24 * _clip01(abs(impulse_delta) * 8.0)
            + 0.12 * _clip01(latest_body_height_pct * 4.0)
        )
        target_delta = int(round(min(height * (0.055 + 0.145 * move_strength), max(band_height * 2.2, (fit_bottom - fit_top) * 0.30))))

        direction = str(execution_action or "").upper()
        if direction not in {"BUY", "SELL"}:
            direction = str(candidate_action or "").upper()
        if direction not in {"BUY", "SELL"} and local_direction == impulse_direction:
            direction = str(local_direction or "").upper()
        if direction not in {"BUY", "SELL"}:
            return {
                "direction": "HOLD",
                "confidence": confidence,
                "message": "No clean future trigger zone until local structure chooses a side.",
                "zones": [],
            }

        projection_confidence = _clip01(0.64 * confidence + 0.24 * move_strength + 0.12 * _clip01(len(candles) / 16.0))
        zones: list[dict[str, Any]] = []
        sign_y = -1 if direction == "BUY" else 1
        aggressive_band = max(8, int(round(band_height * 0.70)))
        entry_anchor = latest_y + sign_y * band_height * 0.62
        memory_anchor = recent_top - band_height * 0.55 if direction == "BUY" else recent_bottom + band_height * 0.55
        entry_anchor = (entry_anchor * 0.64) + (memory_anchor * 0.36)
        entry_center_y = int(round(max(vertical_min, min(vertical_max, entry_anchor))))
        target_center_y = int(round(max(vertical_min, min(vertical_max, entry_center_y + sign_y * target_delta))))
        if abs(target_center_y - entry_center_y) < band_height * 1.35:
            target_center_y = int(round(max(vertical_min, min(vertical_max, entry_center_y + sign_y * band_height * 1.7))))
        invalidation_y = int(
            round(
                max(
                    fit_top + 4,
                    min(
                        fit_bottom - 4,
                        recent_bottom + band_height * 1.45 if direction == "BUY" else recent_top - band_height * 1.45,
                    ),
                )
            )
        )
        sniper_x0 = int(round(max(4.0, min(float(future_x0), latest_x + step_x * 0.05))))
        sniper_x1 = int(round(min(float(right_limit), sniper_x0 + max(30.0, zone_width * 0.72))))
        if sniper_x1 <= sniper_x0 + 12:
            sniper_x0 = future_x0
            sniper_x1 = future_x1
        if direction == "BUY":
            raw_sniper_y = latest_y + aggressive_band * 0.70
            sniper_limit = invalidation_y - band_height - 3
            sniper_center_y = int(round(min(raw_sniper_y, sniper_limit)))
        else:
            raw_sniper_y = latest_y - aggressive_band * 0.70
            sniper_limit = invalidation_y + band_height + 3
            sniper_center_y = int(round(max(raw_sniper_y, sniper_limit)))
        sniper_center_y = int(round(max(vertical_min, min(vertical_max, sniper_center_y))))
        latest_anchor_y = int(round(max(vertical_min, min(vertical_max, latest_y))))
        trigger_center_x = int(round((future_x0 + future_x1) * 0.5))
        sniper_center_x = int(round((sniper_x0 + sniper_x1) * 0.5))
        target_center_x = int(round(future_x1 - max(6.0, step_x * 0.22)))
        zones.append(
            {
                "kind": "sniper",
                "direction": direction,
                "label": f"{direction} AGGRO SNIPER",
                "confidence": _clip01(projection_confidence * 0.96),
                "bbox": [sniper_x0, sniper_center_y - aggressive_band, sniper_x1, sniper_center_y + aggressive_band],
                "invalidation_y": invalidation_y,
                "entry_mode": "aggressive_sniper",
                "instruction": (
                    "Aggressive first-touch watch. Enter on tap plus immediate reject/reclaim "
                    f"back into {direction} pressure."
                ),
            }
        )
        zones.append(
            {
                "kind": "primary",
                "direction": direction,
                "label": f"{direction} RECLAIM TRIGGER",
                "confidence": projection_confidence,
                "bbox": [future_x0, entry_center_y - aggressive_band, future_x1, entry_center_y + aggressive_band],
                "target_bbox": [
                    future_x0,
                    target_center_y - band_height,
                    future_x1,
                    target_center_y + band_height,
                ],
                "invalidation_y": invalidation_y,
                "entry_mode": "aggressive_sniper",
                "path": [
                    [int(round(latest_x)), latest_anchor_y],
                    [sniper_center_x, sniper_center_y],
                    [trigger_center_x, entry_center_y],
                    [target_center_x, target_center_y],
                ],
            }
        )

        if global_direction in {"BUY", "SELL"} and local_direction in {"BUY", "SELL"} and global_direction != local_direction:
            reclaim_direction = str(global_direction)
            reclaim_sign_y = -1 if reclaim_direction == "BUY" else 1
            reclaim_anchor = recent_top - band_height * 0.75 if reclaim_direction == "BUY" else recent_bottom + band_height * 0.75
            reclaim_center_y = int(round(max(vertical_min, min(vertical_max, reclaim_anchor))))
            reclaim_target_y = int(
                round(max(vertical_min, min(vertical_max, reclaim_center_y + reclaim_sign_y * target_delta * 0.70)))
            )
            zones.append(
                {
                    "kind": "alternate",
                    "direction": reclaim_direction,
                    "label": f"{reclaim_direction} RECLAIM",
                    "confidence": _clip01(projection_confidence * 0.78),
                    "bbox": [
                        future_x0,
                        reclaim_center_y - band_height,
                        future_x1,
                        reclaim_center_y + band_height,
                    ],
                    "target_bbox": [
                        future_x0,
                        reclaim_target_y - band_height,
                        future_x1,
                        reclaim_target_y + band_height,
                    ],
                    "path": [
                        [int(round(latest_x)), latest_anchor_y],
                        [trigger_center_x, reclaim_center_y],
                        [target_center_x, reclaim_target_y],
                    ],
                }
            )

        return {
            "direction": direction,
            "confidence": projection_confidence,
            "fit_bounds": [4, fit_top, right_limit, fit_bottom],
            "message": (
                f"{direction} sniper watch is the early area; "
                "the trigger is confirmation and invalidation is cancel/no-trade."
            ),
            "zones": zones,
            "instructions": [
                "SNIPER WATCH is the early area, but it still needs rejection/reclaim.",
                "TRIGGER is the confirmation entry and can appear later.",
                "INVALIDATE cancels this setup; it is not an entry.",
            ],
        }

    def _build_candle_statistics(
        self,
        candles: Sequence[Mapping[str, Any]],
        *,
        candidate_action: str,
    ) -> dict[str, Any]:
        rows = [dict(item) for item in candles]
        sample_size = len(rows)
        if sample_size <= 0:
            return {
                "sample_size": 0,
                "sample_weight": 0.0,
                "buy_count": 0,
                "sell_count": 0,
                "buy_ratio": 0.0,
                "sell_ratio": 0.0,
                "recent_buy_ratio": 0.0,
                "recent_sell_ratio": 0.0,
                "direction_run": 0,
                "opposite_run": 0,
                "candidate_ratio": 0.0,
                "opposing_ratio": 0.0,
                "momentum_consistency": 0.0,
                "normalized_volatility": 0.0,
                "average_step": 0.0,
            }

        directions = [str(item.get("direction", "HOLD") or "HOLD").upper() for item in rows]
        buy_count = sum(1 for direction in directions if direction == "BUY")
        sell_count = sum(1 for direction in directions if direction == "SELL")
        directional_count = max(1, buy_count + sell_count)
        recent = rows[-min(12, sample_size):]
        recent_directions = [str(item.get("direction", "HOLD") or "HOLD").upper() for item in recent]
        recent_buy_count = sum(1 for direction in recent_directions if direction == "BUY")
        recent_sell_count = sum(1 for direction in recent_directions if direction == "SELL")
        recent_directional_count = max(1, recent_buy_count + recent_sell_count)

        normalized_candidate = str(candidate_action or "HOLD").upper()
        candidate_ratio = (
            float(recent_buy_count / recent_directional_count)
            if normalized_candidate == "BUY"
            else float(recent_sell_count / recent_directional_count)
            if normalized_candidate == "SELL"
            else 0.0
        )
        opposing_ratio = (
            float(recent_sell_count / recent_directional_count)
            if normalized_candidate == "BUY"
            else float(recent_buy_count / recent_directional_count)
            if normalized_candidate == "SELL"
            else 0.0
        )

        latest_direction = directions[-1] if directions else "HOLD"
        direction_run = 0
        for direction in reversed(directions):
            if direction != latest_direction:
                break
            direction_run += 1
        opposite_run = 0
        if normalized_candidate in {"BUY", "SELL"}:
            opposite = "SELL" if normalized_candidate == "BUY" else "BUY"
            for direction in reversed(directions):
                if direction != opposite:
                    break
                opposite_run += 1

        proxies = [float(item.get("price_proxy", 0.0) or 0.0) for item in rows]
        deltas = [right - left for left, right in zip(proxies, proxies[1:])]
        direction_sign = 1.0 if normalized_candidate == "BUY" else -1.0 if normalized_candidate == "SELL" else 0.0
        aligned_steps = sum(1 for delta in deltas[-12:] if direction_sign != 0.0 and delta * direction_sign > 0.0)
        inspected_steps = max(1, min(12, len(deltas)))
        momentum_consistency = float(aligned_steps / inspected_steps) if deltas else 0.0
        absolute_steps = [abs(delta) for delta in deltas[-12:]]
        average_step = float(sum(absolute_steps) / max(1, len(absolute_steps))) if absolute_steps else 0.0
        normalized_volatility = _clip01(average_step * 9.0)
        sample_weight = _clip01(0.18 + 0.82 * _clip01((sample_size - 5) / 31.0))

        return {
            "sample_size": int(sample_size),
            "sample_weight": sample_weight,
            "buy_count": int(buy_count),
            "sell_count": int(sell_count),
            "buy_ratio": float(buy_count / directional_count),
            "sell_ratio": float(sell_count / directional_count),
            "recent_buy_count": int(recent_buy_count),
            "recent_sell_count": int(recent_sell_count),
            "recent_buy_ratio": float(recent_buy_count / recent_directional_count),
            "recent_sell_ratio": float(recent_sell_count / recent_directional_count),
            "direction_run": int(direction_run),
            "opposite_run": int(opposite_run),
            "candidate_ratio": _clip01(candidate_ratio),
            "opposing_ratio": _clip01(opposing_ratio),
            "momentum_consistency": _clip01(momentum_consistency),
            "normalized_volatility": normalized_volatility,
            "average_step": average_step,
        }

    def _build_behavior_payload(
        self,
        candles: Sequence[Mapping[str, Any]],
        projection: Mapping[str, Any],
        candle_statistics: Mapping[str, Any],
        *,
        candidate_action: str,
        global_direction: str,
        local_direction: str,
        impulse_direction: str,
        global_slope: float,
        local_slope: float,
        current_slope: float,
        recent_range: float,
        consolidation_score: float,
        impulse_score: float,
        reversal_score: float,
    ) -> dict[str, Any]:
        tokens = self._build_candle_behavior_tokens(candles, projection, candidate_action=candidate_action)
        direction = str(projection.get("direction", candidate_action) or candidate_action).upper()
        if direction not in {"BUY", "SELL"}:
            direction = str(candidate_action or "HOLD").upper()
        recent_tokens = tokens[-min(12, len(tokens)):]
        previous_tokens = tokens[-min(24, len(tokens)):-min(12, len(tokens))] if len(tokens) > 12 else tokens[:-1]

        event_names = [str(token.get("micro_structure_event", "noise") or "noise") for token in recent_tokens]
        rejection_count = sum(1 for event in event_names if "rejection" in event)
        compression_count = sum(1 for event in event_names if "compression" in event or "inside_bar" in event)
        impulse_count = sum(1 for event in event_names if "impulse" in event or "breakout_attempt" in event)
        pullback_count = sum(1 for event in event_names if "pullback" in event)
        pause_count = sum(1 for event in event_names if "pause" in event or "doji" in event)
        exhaustion_count = sum(1 for event in event_names if "exhaustion" in event)
        reversal_count = sum(1 for event in event_names if "reversal" in event or "failed_breakout" in event)

        current_state = self._infer_sequence_state(
            recent_tokens,
            candidate_action=direction,
            consolidation_score=consolidation_score,
            impulse_score=impulse_score,
            reversal_score=reversal_score,
        )
        previous_state = self._infer_sequence_state(
            previous_tokens,
            candidate_action=direction,
            consolidation_score=consolidation_score * 0.75,
            impulse_score=impulse_score * 0.75,
            reversal_score=reversal_score * 0.75,
        )
        transition_probs = self._transition_forecast(
            current_state,
            candidate_action=direction,
            compression_score=_clip01(compression_count / max(1, len(recent_tokens))),
            failure_risk=_clip01(reversal_score + exhaustion_count / max(1, len(recent_tokens))),
        )
        next_state = "sideways_pause"
        if transition_probs:
            next_state = sorted(transition_probs.items(), key=lambda item: item[1], reverse=True)[0][0]

        sample_weight = _clip01(candle_statistics.get("sample_weight", 0.0))
        state_confidence = _clip01(
            0.28 * sample_weight
            + 0.22 * max(_clip01(rejection_count / 3.0), _clip01(impulse_count / 4.0), _clip01(compression_count / 5.0))
            + 0.18 * _clip01(impulse_score)
            + 0.17 * (1.0 - _clip01(consolidation_score) if "continuation" in current_state else _clip01(consolidation_score))
            + 0.15 * (1.0 - _clip01(reversal_score) if "reversal" not in current_state else _clip01(reversal_score))
        )
        compression_score = _clip01(0.55 * consolidation_score + 0.45 * _clip01(compression_count / max(1, len(recent_tokens))))
        momentum_exit_score = _clip01(0.50 * impulse_score + 0.25 * _clip01(impulse_count / 4.0) + 0.25 * _clip01(rejection_count / 3.0))
        failure_risk = _clip01(
            0.35 * reversal_score
            + 0.24 * _clip01(exhaustion_count / max(1, len(recent_tokens)))
            + 0.21 * _clip01(reversal_count / max(1, len(recent_tokens)))
            + 0.20 * _clip01(candle_statistics.get("opposing_ratio", 0.0))
        )
        box_context = self._build_box_behavior_context(
            tokens,
            projection,
            candidate_action=direction,
            rejection_count=rejection_count,
            compression_score=compression_score,
            momentum_exit_score=momentum_exit_score,
            failure_risk=failure_risk,
        )
        trend_context = {
            "global_bias": str(global_direction or "HOLD").upper(),
            "local_bias": self._contextual_bias_label(global_direction, local_direction),
            "micro_bias": self._contextual_bias_label(local_direction, impulse_direction),
            "slope_global": float(global_slope),
            "slope_local": float(local_slope),
            "slope_current": float(current_slope),
            "trend_strength": _clip01(abs(global_slope) * 3.2 + abs(local_slope) * 2.4),
            "recent_range": float(recent_range),
        }
        trend_phase = self._trend_phase_label(current_state, trend_context, compression_score=compression_score)
        move_quality = self._move_quality_label(
            current_state,
            failure_risk=failure_risk,
            compression_score=compression_score,
            sample_weight=sample_weight,
            opposing_ratio=_clip01(candle_statistics.get("opposing_ratio", 0.0)),
        )

        return {
            "current_state": current_state,
            "previous_state": previous_state,
            "next_most_likely_state": next_state,
            "next_state_probs": transition_probs,
            "state_confidence": state_confidence,
            "trend_phase": trend_phase,
            "move_quality": move_quality,
            "behavior_counts": {
                "rejection_count": int(rejection_count),
                "compression_count": int(compression_count),
                "impulse_count": int(impulse_count),
                "pullback_count": int(pullback_count),
                "pause_count": int(pause_count),
                "exhaustion_count": int(exhaustion_count),
                "reversal_count": int(reversal_count),
            },
            "candle_tokens": tokens[-36:],
            "box_context": box_context,
            "trend_context": trend_context,
        }

    def _build_candle_behavior_tokens(
        self,
        candles: Sequence[Mapping[str, Any]],
        projection: Mapping[str, Any],
        *,
        candidate_action: str,
    ) -> list[dict[str, Any]]:
        rows = [dict(item) for item in candles]
        if not rows:
            return []
        fit_bounds = cast(Sequence[Any], projection.get("fit_bounds", []))
        if len(fit_bounds) >= 4:
            fit_top = _float_or(fit_bounds[1], 0.0)
            fit_bottom = max(fit_top + 1.0, _float_or(fit_bounds[3], fit_top + 1.0))
        else:
            tops = [_float_or(cast(Sequence[Any], item.get("bbox", [0, 0, 0, 1]))[1], 0.0) for item in rows]
            bottoms = [_float_or(cast(Sequence[Any], item.get("bbox", [0, 0, 0, 1]))[3], 1.0) for item in rows]
            fit_top = min(tops)
            fit_bottom = max(max(bottoms), fit_top + 1.0)
        fit_height = max(1.0, fit_bottom - fit_top)

        ranges: list[float] = []
        for item in rows:
            bbox = cast(Sequence[Any], item.get("bbox", []))
            if len(bbox) >= 4:
                ranges.append(max(1.0, abs(_float_or(bbox[3]) - _float_or(bbox[1]))))
        median_range = float(np.median(np.asarray(ranges, dtype=np.float32))) if ranges else 1.0
        zones = self._extract_projection_zone_geometry(projection)
        tokens: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        candidate = str(candidate_action or "HOLD").upper()
        for index, item in enumerate(rows, start=1):
            bbox = cast(Sequence[Any], item.get("bbox", []))
            if len(bbox) < 4:
                continue
            x0, y0, x1, y1 = [_float_or(value) for value in bbox[:4]]
            top = min(y0, y1)
            bottom = max(y0, y1)
            center_y = _float_or(item.get("center_y", (top + bottom) * 0.5), (top + bottom) * 0.5)
            candle_range = max(1.0, bottom - top)
            direction = str(item.get("direction", "HOLD") or "HOLD").upper()
            direction_value = 1 if direction == "BUY" else -1 if direction == "SELL" else 0
            open_y = bottom if direction == "BUY" else top if direction == "SELL" else center_y
            close_y = top if direction == "BUY" else bottom if direction == "SELL" else center_y
            high_proxy = _clip01(1.0 - ((top - fit_top) / fit_height))
            low_proxy = _clip01(1.0 - ((bottom - fit_top) / fit_height))
            open_proxy = _clip01(1.0 - ((open_y - fit_top) / fit_height))
            close_proxy = _clip01(1.0 - ((close_y - fit_top) / fit_height))
            range_norm = float(candle_range / max(1.0, median_range))
            close_position = _clip01((close_proxy - low_proxy) / max(1e-6, high_proxy - low_proxy))
            previous_high = _float_or(previous.get("high_proxy"), high_proxy) if previous else high_proxy
            previous_low = _float_or(previous.get("low_proxy"), low_proxy) if previous else low_proxy
            breaks_prev_high = high_proxy > previous_high + 0.012
            breaks_prev_low = low_proxy < previous_low - 0.012
            inside_bar = bool(previous and high_proxy <= previous_high + 0.006 and low_proxy >= previous_low - 0.006)
            outside_bar = bool(previous and high_proxy > previous_high + 0.006 and low_proxy < previous_low - 0.006)
            engulfing = bool(outside_bar and direction_value != 0 and direction == candidate)
            volatility_state = (
                "expanding"
                if range_norm >= 1.18
                else "contracting"
                if range_norm <= 0.78
                else "normal"
            )
            distance_to_sniper = self._normalized_distance_to_y_zone(center_y, zones.get("sniper"), fit_height)
            distance_to_trigger = self._normalized_distance_to_y_zone(center_y, zones.get("trigger"), fit_height)
            distance_to_target = self._normalized_distance_to_y_zone(center_y, zones.get("target"), fit_height)
            distance_to_invalidation = self._normalized_distance_to_y_line(center_y, zones.get("invalidation_y"), fit_height)
            event = self._classify_micro_structure_event(
                direction=direction,
                candidate_action=candidate,
                range_norm=range_norm,
                close_position=close_position,
                breaks_prev_high=breaks_prev_high,
                breaks_prev_low=breaks_prev_low,
                inside_bar=inside_bar,
                outside_bar=outside_bar,
                distance_to_sniper=distance_to_sniper,
                distance_to_invalidation=distance_to_invalidation,
                volatility_state=volatility_state,
            )
            token = {
                "index": int(index),
                "track_id": int(item.get("track_id", index) or index),
                "direction": direction,
                "direction_value": int(direction_value),
                "bbox": [int(round(x0)), int(round(top)), int(round(x1)), int(round(bottom))],
                "ohlc_source": "bbox_estimate",
                "open_proxy": open_proxy,
                "high_proxy": high_proxy,
                "low_proxy": low_proxy,
                "close_proxy": close_proxy,
                "body_pct": _clip01(0.46 + min(0.34, max(0.0, range_norm - 0.75) * 0.32)),
                "upper_wick_pct": _clip01(0.18 if direction == "BUY" else 0.34 if direction == "SELL" else 0.26),
                "lower_wick_pct": _clip01(0.34 if direction == "BUY" else 0.18 if direction == "SELL" else 0.26),
                "range_norm": float(range_norm),
                "close_position": close_position,
                "breaks_prev_high": bool(breaks_prev_high),
                "breaks_prev_low": bool(breaks_prev_low),
                "inside_bar": bool(inside_bar),
                "outside_bar": bool(outside_bar),
                "engulfing": bool(engulfing),
                "distance_to_sniper": distance_to_sniper,
                "distance_to_trigger": distance_to_trigger,
                "distance_to_invalidation": distance_to_invalidation,
                "distance_to_target": distance_to_target,
                "volatility_state": volatility_state,
                "micro_structure_event": event,
            }
            tokens.append(token)
            previous = token
        return tokens

    def _extract_projection_zone_geometry(self, projection: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for raw_zone in cast(Sequence[Any], projection.get("zones", [])):
            zone = _mapping_to_dict(raw_zone)
            kind = str(zone.get("kind", "") or "").lower()
            bbox = cast(Sequence[Any], zone.get("bbox", []))
            if len(bbox) >= 4:
                if kind == "sniper":
                    result["sniper"] = list(bbox[:4])
                elif kind == "primary":
                    result["trigger"] = list(bbox[:4])
                    target = cast(Sequence[Any], zone.get("target_bbox", []))
                    if len(target) >= 4:
                        result["target"] = list(target[:4])
            if "invalidation_y" in zone:
                result["invalidation_y"] = zone.get("invalidation_y")
        return result

    def _normalized_distance_to_y_zone(self, center_y: float, bbox: Any, fit_height: float) -> float:
        row = cast(Sequence[Any], bbox if isinstance(bbox, (list, tuple)) else [])
        if len(row) < 4:
            return 1.0
        top = min(_float_or(row[1]), _float_or(row[3]))
        bottom = max(_float_or(row[1]), _float_or(row[3]))
        if top <= center_y <= bottom:
            return 0.0
        return _clip01(min(abs(center_y - top), abs(center_y - bottom)) / max(1.0, fit_height))

    def _normalized_distance_to_y_line(self, center_y: float, line_y: Any, fit_height: float) -> float:
        if line_y is None:
            return 1.0
        return _clip01(abs(center_y - _float_or(line_y, center_y)) / max(1.0, fit_height))

    def _classify_micro_structure_event(
        self,
        *,
        direction: str,
        candidate_action: str,
        range_norm: float,
        close_position: float,
        breaks_prev_high: bool,
        breaks_prev_low: bool,
        inside_bar: bool,
        outside_bar: bool,
        distance_to_sniper: float,
        distance_to_invalidation: float,
        volatility_state: str,
    ) -> str:
        candidate = str(candidate_action or "HOLD").upper()
        if inside_bar and volatility_state == "contracting":
            return "compression_inside_bar"
        if inside_bar:
            return "sideways_pause"
        if direction == candidate and distance_to_sniper <= 0.08 and (
            (candidate == "BUY" and close_position >= 0.62) or (candidate == "SELL" and close_position <= 0.38)
        ):
            return "bullish_rejection" if candidate == "BUY" else "bearish_rejection"
        if direction == candidate and range_norm >= 1.16 and (
            (candidate == "BUY" and breaks_prev_high) or (candidate == "SELL" and breaks_prev_low)
        ):
            return "bullish_impulse" if candidate == "BUY" else "bearish_impulse"
        if direction != candidate and candidate in {"BUY", "SELL"} and distance_to_sniper <= 0.16 and range_norm <= 1.12:
            return "bullish_pullback_into_zone" if candidate == "BUY" else "bearish_pullback_into_zone"
        if direction != candidate and candidate in {"BUY", "SELL"} and range_norm >= 1.22 and distance_to_invalidation <= 0.12:
            return "reversal_attempt"
        if outside_bar and direction != candidate:
            return "failed_breakout"
        if range_norm <= 0.72:
            return "pause_doji_like"
        if volatility_state == "expanding" and direction != candidate:
            return "exhaustion_against_bias"
        return "bullish_continuation" if direction == "BUY" else "bearish_continuation" if direction == "SELL" else "noise"

    def _infer_sequence_state(
        self,
        tokens: Sequence[Mapping[str, Any]],
        *,
        candidate_action: str,
        consolidation_score: float,
        impulse_score: float,
        reversal_score: float,
    ) -> str:
        if not tokens:
            return "noise"
        candidate = str(candidate_action or "HOLD").upper()
        events = [str(token.get("micro_structure_event", "noise") or "noise") for token in tokens]
        rejection_count = sum(1 for event in events if "rejection" in event)
        compression_count = sum(1 for event in events if "compression" in event or "inside_bar" in event)
        pause_count = sum(1 for event in events if "pause" in event or "doji" in event)
        pullback_count = sum(1 for event in events if "pullback" in event)
        impulse_count = sum(1 for event in events if "impulse" in event)
        reversal_count = sum(1 for event in events if "reversal" in event or "failed_breakout" in event)
        exhaustion_count = sum(1 for event in events if "exhaustion" in event)
        candidate_count = sum(1 for token in tokens if str(token.get("direction", "HOLD") or "HOLD").upper() == candidate)
        opposing_count = sum(1 for token in tokens if str(token.get("direction", "HOLD") or "HOLD").upper() not in {candidate, "HOLD"})
        total = max(1, len(tokens))
        prefix = "bullish" if candidate == "BUY" else "bearish" if candidate == "SELL" else "neutral"
        if reversal_score >= 0.56 or reversal_count >= 2:
            return "confirmed_reversal" if reversal_count >= 3 and opposing_count > candidate_count else "reversal_attempt"
        if exhaustion_count >= 2 or (impulse_count >= 3 and _clip01(consolidation_score) >= 0.42):
            return "exhaustion"
        if (compression_count + pause_count) >= 2 and candidate_count >= opposing_count and _clip01(impulse_score) >= 0.34:
            return f"{prefix}_continuation_reload"
        if compression_count >= 3 or _clip01(consolidation_score) >= 0.68:
            return "compression"
        if rejection_count >= 2:
            return f"{prefix}_rejection_building"
        if pullback_count >= 2 and candidate_count >= max(1, opposing_count - 1):
            return f"{prefix}_pullback"
        if impulse_count >= 2 or (_clip01(impulse_score) >= 0.62 and candidate_count / total >= 0.55):
            return f"{prefix}_continuation"
        if opposing_count / total >= 0.58:
            return "failed_breakout" if candidate in {"BUY", "SELL"} else "sideways_pause"
        return "sideways_pause"

    def _transition_forecast(
        self,
        current_state: str,
        *,
        candidate_action: str,
        compression_score: float,
        failure_risk: float,
    ) -> dict[str, float]:
        candidate = str(candidate_action or "HOLD").upper()
        prefix = "bullish" if candidate == "BUY" else "bearish" if candidate == "SELL" else "neutral"
        state = str(current_state or "noise").lower()
        if "rejection" in state:
            raw = {f"{prefix}_continuation": 0.52, f"{prefix}_pullback": 0.16, "sideways_pause": 0.14, "failed_breakout": 0.10, "reversal_attempt": 0.08}
        elif "pullback" in state:
            raw = {f"{prefix}_rejection_building": 0.36, f"{prefix}_continuation": 0.25, "sideways_pause": 0.18, "failed_breakout": 0.11, "reversal_attempt": 0.10}
        elif "continuation" in state:
            raw = {f"{prefix}_continuation": 0.39, f"{prefix}_pullback": 0.25, "exhaustion": 0.14, "sideways_pause": 0.12, "reversal_attempt": 0.10}
        elif "compression" in state:
            raw = {"breakout_attempt": 0.34, "sideways_pause": 0.24, f"{prefix}_continuation": 0.20, "failed_breakout": 0.14, "reversal_attempt": 0.08}
        elif "exhaustion" in state:
            raw = {"reversal_attempt": 0.34, "sideways_pause": 0.24, "failed_breakout": 0.18, f"{prefix}_pullback": 0.14, f"{prefix}_continuation": 0.10}
        elif "reversal" in state or "failed" in state:
            raw = {"confirmed_reversal": 0.34, "failed_breakout": 0.22, "sideways_pause": 0.18, f"{prefix}_rejection_building": 0.14, f"{prefix}_continuation": 0.12}
        else:
            raw = {"sideways_pause": 0.36, "compression": 0.22, f"{prefix}_pullback": 0.16, f"{prefix}_continuation": 0.14, "reversal_attempt": 0.12}
        raw["sideways_pause"] = raw.get("sideways_pause", 0.0) + 0.10 * _clip01(compression_score)
        raw["reversal_attempt"] = raw.get("reversal_attempt", 0.0) + 0.12 * _clip01(failure_risk)
        total = max(1e-6, sum(raw.values()))
        return {key: _clip01(value / total) for key, value in raw.items()}

    def _build_box_behavior_context(
        self,
        tokens: Sequence[Mapping[str, Any]],
        projection: Mapping[str, Any],
        *,
        candidate_action: str,
        rejection_count: int,
        compression_score: float,
        momentum_exit_score: float,
        failure_risk: float,
    ) -> dict[str, Any]:
        direction = str(candidate_action or "HOLD").upper()
        entry_state = str(projection.get("entry_state", "") or "").upper()
        box_type = f"sniper_{direction.lower()}" if "SNIPER" in entry_state or projection.get("zones") else f"projection_{direction.lower()}"
        candles_seen_in_box = sum(1 for token in tokens if _clip01(token.get("distance_to_sniper", 1.0)) <= 0.10)
        acceptance_count = sum(
            1
            for token in tokens[-12:]
            if str(token.get("direction", "HOLD") or "HOLD").upper() == direction
            and (
                "impulse" in str(token.get("micro_structure_event", "") or "")
                or "continuation" in str(token.get("micro_structure_event", "") or "")
                or _clip01(token.get("distance_to_trigger", 1.0)) <= 0.08
            )
        )
        return {
            "box_type": box_type,
            "candles_seen_in_box": int(candles_seen_in_box),
            "entry_quality": _clip01(projection.get("confidence", 0.0)),
            "rejection_count": int(rejection_count),
            "acceptance_count": int(acceptance_count),
            "compression_score": _clip01(compression_score),
            "momentum_exit_score": _clip01(momentum_exit_score),
            "failure_risk": _clip01(failure_risk),
            "behavior_state": (
                "respecting_buy_zone"
                if direction == "BUY" and rejection_count > 0
                else "respecting_sell_zone"
                if direction == "SELL" and rejection_count > 0
                else "waiting_for_reaction"
            ),
        }

    def _contextual_bias_label(self, base_direction: str, child_direction: str) -> str:
        base = str(base_direction or "HOLD").upper()
        child = str(child_direction or "HOLD").upper()
        if child not in {"BUY", "SELL"}:
            return "HOLD"
        if base in {"BUY", "SELL"} and child != base:
            return f"{child}_PULLBACK"
        return f"{child}_RECOVERY" if base == "HOLD" else child

    def _trend_phase_label(
        self,
        current_state: str,
        trend_context: Mapping[str, Any],
        *,
        compression_score: float,
    ) -> str:
        state = str(current_state or "").lower()
        if "rejection" in state:
            return "pause_then_resume"
        if "continuation" in state:
            return "trend_expansion"
        if "compression" in state or compression_score >= 0.62:
            return "compression_before_breakout"
        if "reversal" in state or "failed" in state:
            return "reversal_risk"
        if abs(_float_or(trend_context.get("slope_current"), 0.0)) < abs(_float_or(trend_context.get("slope_global"), 0.0)) * 0.45:
            return "trend_pause"
        return "mixed_transition"

    def _move_quality_label(
        self,
        current_state: str,
        *,
        failure_risk: float,
        compression_score: float,
        sample_weight: float,
        opposing_ratio: float,
    ) -> str:
        state = str(current_state or "").lower()
        if sample_weight < 0.34:
            return "low_sample"
        if failure_risk >= 0.55 or "reversal" in state or "failed" in state:
            return "unstable"
        if compression_score >= 0.62:
            return "coiling"
        if opposing_ratio >= 0.42:
            return "choppy"
        if "rejection" in state or "continuation" in state:
            return "clean"
        return "mixed"

    def _build_projection_probability_payload(
        self,
        candle_statistics: Mapping[str, Any],
        projection: Mapping[str, Any],
        entry_plan: Mapping[str, Any],
        behavior_payload: Mapping[str, Any],
        *,
        candidate_action: str,
        global_direction: str,
        local_direction: str,
        impulse_direction: str,
        confidence: float,
        consolidation_score: float,
        continuation_score: float,
        impulse_score: float,
        reversal_score: float,
    ) -> dict[str, Any]:
        direction = str(projection.get("direction", candidate_action) or candidate_action).upper()
        sample_weight = _clip01(candle_statistics.get("sample_weight", 0.0))
        if direction not in {"BUY", "SELL"}:
            return {
                "target_first_probability": 0.08,
                "invalidation_first_probability": 0.12,
                "sideways_probability": 0.80,
                "expected_candles_to_resolution": [3, 8],
                "sample_weight": sample_weight,
                "probability_state": "NO_EDGE",
                "probability_reason": "No clean direction yet; continuous prediction stays in no-trade mode.",
                "candle_statistics": dict(candle_statistics),
            }

        entry_state = str(entry_plan.get("entry_state", "WAIT") or "WAIT").upper()
        timing_score = _clip01(entry_plan.get("timing_score", 0.0))
        behavior_state = str(behavior_payload.get("current_state", "") or "").lower()
        next_behavior_state = str(behavior_payload.get("next_most_likely_state", "") or "").lower()
        box_context = _mapping_to_dict(behavior_payload.get("box_context", {}))
        failure_risk = _clip01(box_context.get("failure_risk", 0.0))
        behavior_confidence = _clip01(behavior_payload.get("state_confidence", 0.0))
        count_alignment = _clip01(candle_statistics.get("candidate_ratio", 0.0))
        opposing_ratio = _clip01(candle_statistics.get("opposing_ratio", 0.0))
        momentum_consistency = _clip01(candle_statistics.get("momentum_consistency", 0.0))
        volatility = _clip01(candle_statistics.get("normalized_volatility", 0.0))
        latest_run = int(candle_statistics.get("direction_run", 0) or 0)
        opposing_run = int(candle_statistics.get("opposite_run", 0) or 0)
        alignment_score = _clip01(
            (0.34 if str(global_direction).upper() == direction else 0.0)
            + (0.30 if str(local_direction).upper() == direction else 0.0)
            + (0.20 if str(impulse_direction).upper() == direction else 0.0)
            + (0.16 * count_alignment)
        )
        run_quality = _clip01(latest_run / 5.0) if opposing_run == 0 else _clip01(0.45 - opposing_run * 0.12)

        if entry_state == "COMPLETE":
            target_probability = 0.94
            invalidation_probability = 0.02
            sideways_probability = 0.04
        elif entry_state == "INVALIDATED":
            target_probability = 0.04
            invalidation_probability = 0.90
            sideways_probability = 0.06
        else:
            target_score = (
                0.16
                + 0.22 * _clip01(confidence)
                + 0.18 * alignment_score
                + 0.15 * count_alignment
                + 0.12 * momentum_consistency
                + 0.08 * continuation_score
                + 0.07 * timing_score
                + 0.04 * run_quality
                + 0.08
                * behavior_confidence
                * (
                    1.0
                    if ("rejection" in behavior_state or "continuation" in behavior_state or "continuation" in next_behavior_state)
                    else 0.0
                )
            )
            invalidation_score = (
                0.12
                + 0.22 * (1.0 - alignment_score)
                + 0.20 * opposing_ratio
                + 0.13 * reversal_score
                + 0.10 * volatility
                + 0.08 * _clip01(opposing_run / 4.0)
                + 0.05 * (1.0 - timing_score)
                + 0.12 * failure_risk
                + 0.08
                * behavior_confidence
                * (1.0 if ("reversal" in behavior_state or "exhaustion" in behavior_state or "failed" in next_behavior_state) else 0.0)
            )
            sideways_score = (
                0.12
                + 0.34 * consolidation_score
                + 0.14 * (1.0 - impulse_score)
                + 0.13 * (1.0 - sample_weight)
                + 0.10 * (1.0 - abs(count_alignment - opposing_ratio))
                + 0.10 * behavior_confidence * (1.0 if ("compression" in behavior_state or "pause" in behavior_state) else 0.0)
            )
            if entry_state in {"SNIPER_READY", "TRIGGER_READY"}:
                target_score += 0.12
                invalidation_score *= 0.88
                sideways_score *= 0.82
            elif entry_state == "SNIPER_WATCH":
                target_score += 0.05
                invalidation_score += 0.03

            score_sum = max(1e-6, target_score + invalidation_score + sideways_score)
            target_probability = target_score / score_sum
            invalidation_probability = invalidation_score / score_sum
            sideways_probability = sideways_score / score_sum

        total_probability = max(1e-6, target_probability + invalidation_probability + sideways_probability)
        target_probability = float(target_probability / total_probability)
        invalidation_probability = float(invalidation_probability / total_probability)
        sideways_probability = float(sideways_probability / total_probability)

        resolution_mid = int(
            round(
                2
                + (1.0 - _clip01(impulse_score)) * 4.0
                + (1.0 - sample_weight) * 3.0
                + _clip01(consolidation_score) * 2.0
                - min(1.0, volatility) * 1.2
            )
        )
        resolution_mid = max(1, min(12, resolution_mid))
        resolution_window = [max(1, resolution_mid - 1), min(14, resolution_mid + 2)]

        if entry_state == "COMPLETE":
            probability_state = "TARGET_REACHED"
            reason = "Target zone is already reached; the move is complete and fresh triggers are blocked."
        elif entry_state == "INVALIDATED":
            probability_state = "INVALIDATION_RISK"
            reason = "Invalidation was hit; target-first probability is suppressed until a new setup forms."
        elif target_probability >= 0.58 and target_probability >= invalidation_probability + 0.16:
            probability_state = "TARGET_FAVORED"
            reason = f"{direction} target-first is favored by aligned candle count and trend pressure."
        elif invalidation_probability >= target_probability:
            probability_state = "INVALIDATION_RISK"
            reason = "Failure risk is elevated by opposing candles, reversal pressure, or weak timing."
        elif sideways_probability >= 0.34:
            probability_state = "RANGE_RISK"
            reason = "Sideways risk is elevated; wait for cleaner acceptance before trusting the projection."
        else:
            probability_state = "MIXED_EDGE"
            reason = "Target-first has an edge, but the candle count is not dominant enough for a clean read."

        return {
            "target_first_probability": _clip01(target_probability),
            "invalidation_first_probability": _clip01(invalidation_probability),
            "sideways_probability": _clip01(sideways_probability),
            "expected_candles_to_resolution": resolution_window,
            "sample_weight": sample_weight,
            "probability_state": probability_state,
            "probability_reason": reason,
            "last_update_candle_count": int(candle_statistics.get("sample_size", 0) or 0),
            "candle_statistics": dict(candle_statistics),
        }

    def _entry_timing_context(
        self,
        candles: Sequence[Mapping[str, Any]],
        projection: Mapping[str, Any],
        *,
        direction: str,
    ) -> dict[str, Any]:
        normalized = _upper_action(direction)
        if normalized not in {"BUY", "SELL"} or not candles:
            return {
                "direction": normalized,
                "target_reached": False,
                "candles_until_sniper_zone": 0,
                "candles_remaining_in_sniper_zone": 0,
                "candles_to_trigger_zone": 0,
                "candles_to_target_zone": 0,
            }
        latest = _mapping_to_dict(candles[-1])
        latest_bbox = cast(Sequence[Any], latest.get("bbox", []))
        if len(latest_bbox) < 4:
            return {
                "direction": normalized,
                "target_reached": False,
                "candles_until_sniper_zone": 0,
                "candles_remaining_in_sniper_zone": 0,
                "candles_to_trigger_zone": 0,
                "candles_to_target_zone": 0,
            }

        centers_x = [
            float(item.get("center_x", 0.0) or 0.0)
            for item in candles
            if _float_or(item.get("center_x", 0.0), 0.0) > 0.0
        ]
        centers_y = [
            float(item.get("center_y", 0.0) or 0.0)
            for item in candles
            if _float_or(item.get("center_y", 0.0), 0.0) > 0.0
        ]
        x_steps = [right - left for left, right in zip(centers_x, centers_x[1:]) if (right - left) > 1.0]
        y_steps = [abs(right - left) for left, right in zip(centers_y, centers_y[1:]) if abs(right - left) > 0.5]
        step_x = float(np.median(np.asarray(x_steps, dtype=np.float32))) if x_steps else 14.0
        step_y = float(np.median(np.asarray(y_steps, dtype=np.float32))) if y_steps else 8.0
        step_x = max(1.0, step_x)
        step_y = max(1.0, step_y)

        latest_x = float(latest.get("center_x", 0.0) or (float(latest_bbox[0]) + float(latest_bbox[2])) * 0.5)
        latest_top = min(float(latest_bbox[1]), float(latest_bbox[3]))
        latest_bottom = max(float(latest_bbox[1]), float(latest_bbox[3]))
        latest_y = float(latest.get("center_y", (latest_top + latest_bottom) * 0.5) or (latest_top + latest_bottom) * 0.5)

        primary_zone: dict[str, Any] = {}
        sniper_zone: dict[str, Any] = {}
        for raw_zone in cast(Sequence[Any], projection.get("zones", [])):
            zone = _mapping_to_dict(raw_zone)
            kind = str(zone.get("kind", "") or "").lower()
            if kind == "primary" and not primary_zone:
                primary_zone = zone
            elif kind == "sniper" and not sniper_zone:
                sniper_zone = zone

        def bbox_bounds(raw_bbox: Any) -> tuple[float, float, float, float] | None:
            if not isinstance(raw_bbox, Sequence) or isinstance(raw_bbox, (str, bytes, bytearray)) or len(raw_bbox) < 4:
                return None
            try:
                x0, y0, x1, y1 = [float(value) for value in raw_bbox[:4]]
            except (TypeError, ValueError):
                return None
            return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

        def candle_overlaps_bbox(candle: Mapping[str, Any], raw_bbox: Any) -> bool:
            bounds = bbox_bounds(raw_bbox)
            candle_bbox = cast(Sequence[Any], candle.get("bbox", []))
            if bounds is None or len(candle_bbox) < 4:
                return False
            bx0, by0, bx1, by1 = bounds
            cx0 = min(float(candle_bbox[0]), float(candle_bbox[2]))
            cy0 = min(float(candle_bbox[1]), float(candle_bbox[3]))
            cx1 = max(float(candle_bbox[0]), float(candle_bbox[2]))
            cy1 = max(float(candle_bbox[1]), float(candle_bbox[3]))
            return cx1 >= bx0 and cx0 <= bx1 and cy1 >= by0 and cy0 <= by1

        def zone_window(raw_bbox: Any) -> dict[str, Any]:
            bounds = bbox_bounds(raw_bbox)
            if bounds is None:
                return {
                    "bbox": [],
                    "capacity_candles": 0,
                    "candles_until": 0,
                    "candles_remaining": 0,
                    "candles_seen": 0,
                    "touching_now": False,
                }
            x0, y0, x1, y1 = bounds
            capacity = max(1, int(np.ceil(max(1.0, x1 - x0) / step_x)))
            touching = bool(latest_bottom >= y0 and latest_top <= y1 and latest_x <= x1 and latest_x >= x0)
            candles_seen = sum(1 for candle in candles[-24:] if candle_overlaps_bbox(candle, [x0, y0, x1, y1]))
            if latest_x < x0:
                until = max(0, int(np.ceil((x0 - latest_x) / step_x)))
                remaining = capacity
            elif latest_x <= x1:
                until = 0
                remaining = max(0, int(np.ceil((x1 - latest_x) / step_x)))
            else:
                until = 0
                remaining = 0
            vertical_steps = int(np.ceil(abs(latest_y - ((y0 + y1) * 0.5)) / step_y))
            return {
                "bbox": [int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))],
                "capacity_candles": int(capacity),
                "candles_until": int(until),
                "candles_remaining": int(remaining),
                "candles_seen": int(candles_seen),
                "touching_now": bool(touching),
                "vertical_candles": int(max(0, vertical_steps)),
            }

        sniper_window = zone_window(sniper_zone.get("bbox", []))
        trigger_window = zone_window(primary_zone.get("bbox", []))
        target_window = zone_window(primary_zone.get("target_bbox", []))
        target_bounds = bbox_bounds(primary_zone.get("target_bbox", []))
        target_reached = False
        target_relation = "unavailable"
        if target_bounds is not None:
            target_x0, target_top, target_x1, target_bottom = target_bounds
            target_x_reached = latest_x >= target_x0 and latest_x <= target_x1
            target_touch = bool(target_x_reached and latest_bottom >= target_top and latest_top <= target_bottom)
            if normalized == "BUY":
                target_reached = bool(target_touch or (target_x_reached and latest_y <= target_bottom))
                target_relation = "at_or_beyond_target" if target_reached else "before_target"
            else:
                target_reached = bool(target_touch or (target_x_reached and latest_y >= target_top))
                target_relation = "at_or_beyond_target" if target_reached else "before_target"

        latest_side = _upper_action(latest.get("direction", "HOLD"))
        post_target_mode = "monitor_continuation" if latest_side == normalized else "monitor_short_pullback"
        candles_to_target = max(
            int(target_window.get("candles_until", 0) or 0),
            int(target_window.get("vertical_candles", 0) or 0),
        )
        return {
            "direction": normalized,
            "target_reached": bool(target_reached),
            "target_relation": target_relation,
            "post_target_monitor": post_target_mode,
            "candles_until_sniper_zone": int(sniper_window.get("candles_until", 0) or 0),
            "candles_remaining_in_sniper_zone": int(sniper_window.get("candles_remaining", 0) or 0),
            "candles_seen_in_sniper_zone": int(sniper_window.get("candles_seen", 0) or 0),
            "sniper_zone_capacity_candles": int(sniper_window.get("capacity_candles", 0) or 0),
            "candles_to_trigger_zone": int(trigger_window.get("candles_until", 0) or 0),
            "candles_remaining_in_trigger_zone": int(trigger_window.get("candles_remaining", 0) or 0),
            "candles_to_target_zone": int(candles_to_target),
            "sniper_touching_now": bool(sniper_window.get("touching_now", False)),
            "trigger_touching_now": bool(trigger_window.get("touching_now", False)),
            "target_touching_now": bool(target_window.get("touching_now", False)),
            "sniper_window": sniper_window,
            "trigger_window": trigger_window,
            "target_window": target_window,
        }

    def _derive_entry_plan(
        self,
        candles: Sequence[Mapping[str, Any]],
        projection: Mapping[str, Any],
        *,
        candidate_action: str,
        global_direction: str,
        local_direction: str,
        impulse_direction: str,
        confidence: float,
        latest_body_height_pct: float,
    ) -> dict[str, Any]:
        direction = str(projection.get("direction", candidate_action) or candidate_action).upper()
        if direction not in {"BUY", "SELL"} or not candles:
            return {
                "execution_action": "HOLD",
                "execution_permission": "WAIT",
                "entry_state": "WAIT",
                "entry_label": "WAIT",
                "entry_stage_label": "WAIT",
                "entry_quality": "NONE",
                "timing_state": "WATCH",
                "timing_score": 0.0,
                "instruction": "No clean entry side yet.",
            }

        latest = candles[-1]
        latest_bbox = cast(Sequence[Any], latest.get("bbox", []))
        if len(latest_bbox) < 4:
            return {
                "execution_action": "HOLD",
                "execution_permission": "WAIT",
                "entry_state": "WAIT",
                "entry_label": f"WAIT {direction}",
                "entry_stage_label": "WAIT",
                "entry_quality": "NONE",
                "timing_state": "WATCH",
                "timing_score": _clip01(confidence * 0.45),
                "instruction": "Waiting for candle geometry before marking an entry.",
            }

        latest_top = min(float(latest_bbox[1]), float(latest_bbox[3]))
        latest_bottom = max(float(latest_bbox[1]), float(latest_bbox[3]))
        latest_center_y = float(latest.get("center_y", (latest_top + latest_bottom) * 0.5) or (latest_top + latest_bottom) * 0.5)
        latest_direction = str(latest.get("direction", "HOLD") or "HOLD").upper()

        primary_zone: dict[str, Any] = {}
        sniper_zone: dict[str, Any] = {}
        for raw_zone in cast(Sequence[Any], projection.get("zones", [])):
            zone = _mapping_to_dict(raw_zone)
            kind = str(zone.get("kind", "") or "").lower()
            if kind == "primary" and not primary_zone:
                primary_zone = zone
            elif kind == "sniper" and not sniper_zone:
                sniper_zone = zone
        timing_context = self._entry_timing_context(candles, projection, direction=direction)

        def enrich(payload: dict[str, Any]) -> dict[str, Any]:
            payload["map_timing"] = dict(timing_context)
            payload["target_reached"] = bool(timing_context.get("target_reached", False))
            payload["post_target_monitor"] = str(timing_context.get("post_target_monitor", "monitor_short_pullback"))
            payload["candles_until_sniper_zone"] = int(timing_context.get("candles_until_sniper_zone", 0) or 0)
            payload["candles_remaining_in_sniper_zone"] = int(timing_context.get("candles_remaining_in_sniper_zone", 0) or 0)
            payload["candles_to_trigger_zone"] = int(timing_context.get("candles_to_trigger_zone", 0) or 0)
            payload["candles_to_target_zone"] = int(timing_context.get("candles_to_target_zone", 0) or 0)
            return payload

        def zone_y_bounds(zone: Mapping[str, Any]) -> tuple[float, float] | None:
            raw_bbox = cast(Sequence[Any], zone.get("bbox", []))
            if len(raw_bbox) < 4:
                return None
            top = min(float(raw_bbox[1]), float(raw_bbox[3]))
            bottom = max(float(raw_bbox[1]), float(raw_bbox[3]))
            return top, bottom

        def candle_overlaps_zone(zone: Mapping[str, Any]) -> bool:
            bounds = zone_y_bounds(zone)
            if bounds is None:
                return False
            zone_top, zone_bottom = bounds
            return latest_bottom >= zone_top and latest_top <= zone_bottom

        def candle_center_inside_zone(zone: Mapping[str, Any]) -> bool:
            bounds = zone_y_bounds(zone)
            if bounds is None:
                return False
            zone_top, zone_bottom = bounds
            return zone_top <= latest_center_y <= zone_bottom

        invalidation_raw = primary_zone.get("invalidation_y", sniper_zone.get("invalidation_y"))
        invalidated = False
        invalidation_y: float | None = None
        try:
            if invalidation_raw is not None:
                invalidation_y = float(invalidation_raw)
        except (TypeError, ValueError):
            invalidation_y = None
        if invalidation_y is not None:
            invalidated = latest_bottom >= invalidation_y if direction == "BUY" else latest_top <= invalidation_y
        if invalidated:
            return enrich({
                "execution_action": "HOLD",
                "execution_permission": "WAIT",
                "entry_state": "INVALIDATED",
                "entry_label": f"CANCEL {direction}",
                "entry_stage_label": "CANCEL",
                "entry_quality": "NONE",
                "timing_state": "INVALIDATED",
                "timing_score": 0.0,
                "instruction": f"{direction} invalidation was hit. This cancels the idea; do not enter there.",
            })
        if bool(timing_context.get("target_reached", False)):
            monitor_mode = str(timing_context.get("post_target_monitor", "monitor_short_pullback"))
            instruction = (
                f"{direction} target zone has already been reached; the move is complete. "
                "Do not send a fresh trigger. Monitor continuation only after a new setup forms."
                if monitor_mode == "monitor_continuation"
                else f"{direction} target zone has already been reached; the move is complete. "
                "Do not send a fresh trigger. Monitor the short pullback or reset."
            )
            return enrich({
                "execution_action": "HOLD",
                "execution_permission": "WAIT",
                "entry_state": "COMPLETE",
                "entry_label": f"TARGET DONE {direction}",
                "entry_stage_label": "TARGET_DONE",
                "entry_quality": "COMPLETE",
                "timing_state": "COMPLETE",
                "timing_score": 0.0,
                "instruction": instruction,
            })

        trend_support = direction in {str(global_direction).upper(), str(local_direction).upper()}
        impulse_support = str(impulse_direction).upper() == direction
        directional_reclaim = bool(
            latest_direction == direction
            and trend_support
            and (impulse_support or latest_body_height_pct >= 0.018)
        )
        sniper_touched = candle_overlaps_zone(sniper_zone)
        primary_touched = candle_overlaps_zone(primary_zone)
        trigger_accepted = candle_center_inside_zone(primary_zone) and latest_direction == direction and impulse_support
        if sniper_touched and not primary_touched and directional_reclaim and confidence >= 0.50:
            return enrich({
                "execution_action": direction,
                "execution_permission": "EXECUTE",
                "entry_state": "SNIPER_READY",
                "entry_label": f"SNIPER {direction}",
                "entry_stage_label": "READY",
                "entry_quality": "SNIPER",
                "timing_state": "READY",
                "timing_score": _clip01(0.52 + confidence * 0.34 + latest_body_height_pct * 1.2),
                "instruction": f"Sniper {direction} is ready: price tapped the watch area and rejected/reclaimed in that direction.",
            })
        if trigger_accepted and confidence >= 0.58:
            return enrich({
                "execution_action": direction,
                "execution_permission": "EXECUTE",
                "entry_state": "TRIGGER_READY",
                "entry_label": f"TRIGGER {direction}",
                "entry_stage_label": "READY",
                "entry_quality": "CONFIRMATION",
                "timing_state": "READY",
                "timing_score": _clip01(0.58 + confidence * 0.34 + latest_body_height_pct * 0.9),
                "instruction": f"Confirmation {direction} is ready: price accepted through the trigger zone.",
            })
        if sniper_touched and not primary_touched:
            return enrich({
                "execution_action": "HOLD",
                "execution_permission": "WAIT",
                "entry_state": "SNIPER_WATCH",
                "entry_label": f"WATCH {direction}",
                "entry_stage_label": "WATCH",
                "entry_quality": "EARLY_WATCH",
                "timing_state": "WATCH",
                "timing_score": _clip01(confidence * 0.72),
                "instruction": f"{direction} watch area is being tested. Wait for rejection/reclaim before entry.",
            })

        wait_state = "WAIT_FOR_TRIGGER" if primary_touched or not sniper_zone else "WAIT_FOR_SNIPER"
        return enrich({
            "execution_action": "HOLD",
            "execution_permission": "WAIT",
            "entry_state": wait_state,
            "entry_label": f"WAIT {direction}",
            "entry_stage_label": "WAIT",
            "entry_quality": "NONE",
            "timing_state": "WATCH",
            "timing_score": _clip01(confidence * 0.52),
            "instruction": f"Bias is {direction}, but price has not reached a valid sniper or trigger condition.",
        })

    def _signal_summary(
        self,
        setup: str,
        candidate_action: str,
        execution_action: str,
        global_direction: str,
        local_direction: str,
        *,
        entry_state: str = "WAIT",
        entry_instruction: str = "",
    ) -> str:
        if candidate_action == "HOLD":
            return f"{setup}. Chart structure is still mixed inside the locked focus region."
        normalized_state = str(entry_state or "WAIT").upper()
        if normalized_state == "INVALIDATED":
            return (
                f"{setup}. {candidate_action} invalidation was hit, so the setup is cancelled. "
                "Invalidation is not an entry."
            )
        if normalized_state == "SNIPER_READY":
            return str(entry_instruction or f"{setup}. Sniper {execution_action} is ready.")
        if normalized_state == "TRIGGER_READY":
            return str(entry_instruction or f"{setup}. Confirmation trigger is ready for {execution_action}.")
        if normalized_state == "SNIPER_WATCH":
            return (
                f"{setup}. {candidate_action} sniper area is being tested, but execution still needs "
                "a rejection/reclaim candle."
            )
        if execution_action == "HOLD":
            return (
                f"{setup}. {candidate_action} pressure is visible, "
                "but the entry gate is waiting for the sniper watch area or trigger acceptance. "
                "Invalidation cancels the setup; it is not an entry."
            )
        return (
            f"{setup}. Global {global_direction} and local {local_direction} structure are aligned, "
            f"so the live surface is reading {execution_action}."
        )

    def _render_overlay(
        self,
        surface_image: Image.Image,
        chart_bbox: Sequence[Any],
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> Image.Image:
        overlay = surface_image.convert("RGBA")
        chart_box = _clip_bbox_to_image(surface_image.size, chart_bbox)
        chart_offset_x = int(chart_box[0])
        chart_offset_y = int(chart_box[1])
        canvas = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas, "RGBA")

        action = str(latest_signal.get("action", "HOLD") or "HOLD").upper()
        projection_view = _mapping_to_dict(tracking_summary.get("projection", {}))
        chart_width = max(1, int(chart_box[2] - chart_box[0]))
        chart_height = max(1, int(chart_box[3] - chart_box[1]))
        chart_radius = max(12, min(20, int(round(min(chart_width, chart_height) * 0.018))))

        hold_rgb: ColorRGB = (138, 160, 181)
        role_colors: dict[str, ColorRGB] = {
            "global": (219, 194, 122),
            "local": (67, 199, 255),
            "current": (
                (96, 218, 145)
                if action == "BUY"
                else (255, 122, 99)
                if action == "SELL"
                else hold_rgb
            ),
        }
        action_rgb = role_colors["current"]

        label_font = _overlay_font(max(11, int(round(surface_image.width * 0.011))), bold=True)
        caption_font = _overlay_font(max(10, int(round(surface_image.width * 0.0095))), bold=False)

        draw.rounded_rectangle(
            chart_box,
            radius=chart_radius,
            outline=(219, 194, 122, 176),
            width=2,
        )

        tracked_candles = [dict(item) for item in cast(Sequence[Any], tracking_summary.get("tracked_candles", []))]
        self._draw_regression_line(
            draw,
            tracked_candles,
            _rgba(role_colors["global"], 232),
            offset=(chart_offset_x, chart_offset_y),
        )
        self._draw_regression_line(
            draw,
            tracked_candles[-8:],
            _rgba(role_colors["local"], 236),
            offset=(chart_offset_x, chart_offset_y),
        )
        self._draw_regression_line(
            draw,
            tracked_candles[-4:],
            _rgba(action_rgb, 244),
            offset=(chart_offset_x, chart_offset_y),
        )
        self._draw_historical_structure_layer(
            draw,
            cast(Sequence[Any], tracking_summary.get("historical_structure", [])),
            chart_box=chart_box,
            offset=(chart_offset_x, chart_offset_y),
            font=caption_font,
        )
        self._draw_support_resistance_layer(
            draw,
            cast(Sequence[Any], tracking_summary.get("support_resistance_zones", [])),
            chart_box=chart_box,
            offset=(chart_offset_x, chart_offset_y),
            font=caption_font,
        )
        structure_boxes = cast(Sequence[Any], tracking_summary.get("structure_boxes", []))
        for raw_box in structure_boxes:
            row = _mapping_to_dict(raw_box)
            key = str(row.get("key", "") or "")
            bbox = cast(Sequence[Any], row.get("bbox", []))
            if len(bbox) < 4:
                continue
            translated = _translate_bbox(bbox, offset_x=chart_offset_x, offset_y=chart_offset_y)
            clipped = _clip_bbox_to_image(surface_image.size, translated)
            label = self._structure_overlay_label(key, tracking_summary, latest_signal)
            fill_alpha = 18 if key == "current" else 0
            self._draw_structure_box(
                draw,
                clipped,
                chart_box=chart_box,
                label=label,
                color=role_colors.get(key, hold_rgb),
                font=label_font,
                fill_alpha=fill_alpha,
            )
            self._draw_structure_micro_plan(
                draw,
                row,
                chart_box=chart_box,
                offset=(chart_offset_x, chart_offset_y),
                color=role_colors.get(key, hold_rgb),
                font=caption_font,
                emphasized=key == "current",
            )
        self._draw_projection_layer(
            draw,
            projection_view,
            chart_box=chart_box,
            offset=(chart_offset_x, chart_offset_y),
            colors=role_colors,
            font=label_font,
        )
        # The full signal report belongs in the dashboard inspector. Keep the
        # chart plane clear so candle bodies and historical/future zones remain legible.
        return Image.alpha_composite(overlay, canvas).convert("RGB")

    def _draw_support_resistance_layer(
        self,
        draw: ImageDraw.ImageDraw,
        zones: Sequence[Any],
        *,
        chart_box: Sequence[Any],
        offset: tuple[float, float],
        font: OverlayFont,
    ) -> None:
        chart_bounds = [int(round(float(value))) for value in chart_box[:4]]
        offset_x = float(offset[0])
        offset_y = float(offset[1])
        for raw_zone in zones:
            zone = _mapping_to_dict(raw_zone)
            bbox = cast(Sequence[Any], zone.get("bbox", []))
            if len(bbox) < 4:
                continue
            role = str(zone.get("role", "") or "").lower()
            color: ColorRGB = (76, 214, 139) if role == "support" else (255, 169, 76)
            translated = _translate_bbox(bbox, offset_x=offset_x, offset_y=offset_y)
            clipped = _clip_bbox_to_bounds(chart_bounds, translated)
            line_y = int(round(_float_or(zone.get("line_y", (float(bbox[1]) + float(bbox[3])) * 0.5)) + offset_y))
            line_y = max(chart_bounds[1] + 2, min(chart_bounds[3] - 2, line_y))
            fill_alpha = 16 if float(zone.get("confidence", 0.0) or 0.0) >= 0.48 else 9
            draw.rounded_rectangle(clipped, radius=10, fill=_rgba(color, fill_alpha), outline=_rgba(color, 88), width=1)
            line_x0 = int(round(_float_or(zone.get("line_x0", float(bbox[0])), float(bbox[0])) + offset_x))
            line_x1 = int(round(_float_or(zone.get("line_x1", float(bbox[2])), float(bbox[2])) + offset_x))
            line_left = max(chart_bounds[0] + 8, min(chart_bounds[2] - 8, line_x0))
            line_right = max(line_left + 8, min(chart_bounds[2] - 8, line_x1))
            self._draw_dashed_line(
                draw,
                (line_left, line_y),
                (line_right, line_y),
                _rgba(color, 164),
                width=2,
                dash=10,
                gap=7,
            )
            label = str(zone.get("label", role.upper() or "LEVEL") or "LEVEL")
            self._draw_overlay_tag(
                draw,
                (chart_bounds[0] + 12, max(chart_bounds[1] + 6, line_y - 28)),
                f"{label} {float(zone.get('confidence', 0.0) or 0.0):.2f}",
                font=font,
                fill=(7, 16, 22, 188),
                outline=_rgba(color, 132),
                text_fill=_rgba(color, 226),
                padding_x=8,
                padding_y=3,
            )

    def _draw_projection_layer(
        self,
        draw: ImageDraw.ImageDraw,
        projection: Mapping[str, Any],
        *,
        chart_box: Sequence[Any],
        offset: tuple[float, float],
        colors: Mapping[str, ColorRGB],
        font: OverlayFont,
    ) -> None:
        zones = cast(Sequence[Any], projection.get("zones", []))
        if not zones:
            return
        chart_left, chart_top, chart_right, chart_bottom = [int(round(float(value))) for value in chart_box[:4]]
        chart_bounds = [chart_left, chart_top, chart_right, chart_bottom]
        offset_x = float(offset[0])
        offset_y = float(offset[1])
        target_first_probability = _clip01(projection.get("target_first_probability", 0.0))
        invalidation_first_probability = _clip01(projection.get("invalidation_first_probability", 0.0))
        for raw_zone in zones:
            zone = _mapping_to_dict(raw_zone)
            direction = str(zone.get("direction", "HOLD") or "HOLD").upper()
            kind = str(zone.get("kind", "primary") or "primary").lower()
            color = (
                (96, 218, 145)
                if direction == "BUY"
                else (255, 122, 99)
                if direction == "SELL"
                else colors.get("current", (138, 160, 181))
            )
            alpha_scale = 1.0 if kind == "primary" else (0.84 if kind == "sniper" else 0.62)
            zone_emphasis = kind in {"primary", "sniper"}
            raw_bbox = cast(Sequence[Any], zone.get("bbox", []))
            raw_target = cast(Sequence[Any], zone.get("target_bbox", []))
            if len(raw_bbox) >= 4:
                bbox = _clip_bbox_to_bounds(chart_bounds, _translate_bbox(raw_bbox, offset_x=offset_x, offset_y=offset_y))
                self._draw_projection_zone(
                    draw,
                    bbox,
                    label=str(zone.get("label", f"{direction} TRIGGER") or f"{direction} TRIGGER"),
                    color=color,
                    font=font,
                    confidence=_clip01(zone.get("confidence", 0.0)),
                    primary=zone_emphasis,
                    bounds=chart_bounds,
                )
            if len(raw_target) >= 4:
                target = _clip_bbox_to_bounds(chart_bounds, _translate_bbox(raw_target, offset_x=offset_x, offset_y=offset_y))
                self._draw_projection_zone(
                    draw,
                    target,
                    label=f"{direction} TARGET {target_first_probability:.0%}" if target_first_probability > 0.0 else f"{direction} TARGET",
                    color=color,
                    font=font,
                    confidence=_clip01(zone.get("confidence", 0.0)),
                    primary=False,
                    bounds=chart_bounds,
                )
            raw_path = cast(Sequence[Any], zone.get("path", []))
            path: list[tuple[int, int]] = []
            for point in raw_path:
                row = cast(Sequence[Any], point)
                if len(row) < 2:
                    continue
                path.append(_clip_point_to_bounds(chart_bounds, (float(row[0]) + offset_x, float(row[1]) + offset_y), pad=6))
            if len(path) >= 2:
                path_color = _rgba(color, int(round(208 * alpha_scale)))
                for start, end in zip(path, path[1:]):
                    self._draw_dashed_line(draw, start, end, path_color, width=3 if kind == "primary" else 2, dash=10, gap=7)
                end_x, end_y = path[-1]
                point_radius = 5 if kind == "primary" else 4
                draw.ellipse(
                    (end_x - point_radius, end_y - point_radius, end_x + point_radius, end_y + point_radius),
                    fill=path_color,
                    outline=(7, 16, 22, 230),
                    width=1,
                )
            if kind == "primary" and "invalidation_y" in zone:
                try:
                    invalidation_y = int(round(float(zone.get("invalidation_y", 0.0)) + offset_y))
                except (TypeError, ValueError):
                    invalidation_y = 0
                if chart_top <= invalidation_y <= chart_bottom:
                    invalidation_color: ColorRGBA = (220, 194, 122, 176)
                    self._draw_dashed_line(
                        draw,
                        (chart_left + 12, invalidation_y),
                        (chart_right - 12, invalidation_y),
                        invalidation_color,
                        width=2,
                        dash=8,
                        gap=8,
                    )
                    self._draw_overlay_tag(
                        draw,
                        (max(chart_left + 16, chart_right - 188), max(chart_top + 8, invalidation_y - 28)),
                        (
                            f"CANCEL / INVALIDATE {invalidation_first_probability:.0%}"
                            if invalidation_first_probability > 0.0
                            else "CANCEL / INVALIDATE"
                        ),
                        font=font,
                        fill=(7, 16, 22, 214),
                        outline=invalidation_color,
                        text_fill=invalidation_color,
                        padding_x=9,
                        padding_y=4,
                    )

    def _draw_historical_structure_layer(
        self,
        draw: ImageDraw.ImageDraw,
        historical_structure: Sequence[Any],
        *,
        chart_box: Sequence[Any],
        offset: tuple[float, float],
        font: OverlayFont,
    ) -> None:
        chart_bounds = [int(round(float(value))) for value in chart_box[:4]]
        offset_x = float(offset[0])
        offset_y = float(offset[1])
        for raw_segment in historical_structure:
            segment = _mapping_to_dict(raw_segment)
            raw_bbox = cast(Sequence[Any], segment.get("bbox", []))
            if len(raw_bbox) < 4:
                continue
            direction = str(segment.get("direction", "HOLD") or "HOLD").upper()
            color: ColorRGB = (
                (96, 218, 145)
                if direction == "BUY"
                else (255, 122, 99)
                if direction == "SELL"
                else (138, 160, 181)
            )
            bbox = _clip_bbox_to_bounds(chart_bounds, _translate_bbox(raw_bbox, offset_x=offset_x, offset_y=offset_y))
            width = max(1, int(bbox[2] - bbox[0]))
            height = max(1, int(bbox[3] - bbox[1]))
            radius = max(8, min(14, int(round(min(width, height) * 0.10))))
            draw.rounded_rectangle(bbox, radius=radius, fill=_rgba(color, 8), outline=_rgba(color, 86), width=1)

            raw_start = cast(Sequence[Any], segment.get("start_point", []))
            raw_end = cast(Sequence[Any], segment.get("end_point", []))
            if len(raw_start) >= 2 and len(raw_end) >= 2:
                start_point = _clip_point_to_bounds(
                    chart_bounds,
                    (float(raw_start[0]) + offset_x, float(raw_start[1]) + offset_y),
                    pad=4,
                )
                end_point = _clip_point_to_bounds(
                    chart_bounds,
                    (float(raw_end[0]) + offset_x, float(raw_end[1]) + offset_y),
                    pad=4,
                )
                self._draw_dashed_line(draw, start_point, end_point, _rgba(color, 156), width=2, dash=7, gap=5)
                point_radius = 3
                draw.ellipse(
                    (
                        end_point[0] - point_radius,
                        end_point[1] - point_radius,
                        end_point[0] + point_radius,
                        end_point[1] + point_radius,
                    ),
                    fill=_rgba(color, 188),
                    outline=(7, 16, 22, 210),
                    width=1,
                )

            label = str(segment.get("label", "") or "").upper()
            if label:
                tag_width, tag_height = self._overlay_tag_size(draw, label, font=font, padding_x=8, padding_y=3)
                label_x = max(chart_bounds[0] + 4, min(int(bbox[0]) + 6, chart_bounds[2] - tag_width - 4))
                label_y = max(chart_bounds[1] + 4, min(int(bbox[1]) + 6, chart_bounds[3] - tag_height - 4))
                self._draw_overlay_tag(
                    draw,
                    (label_x, label_y),
                    label,
                    font=font,
                    fill=(7, 16, 22, 172),
                    outline=_rgba(color, 116),
                    text_fill=_rgba(color, 224),
                    padding_x=8,
                    padding_y=3,
                )

    def _draw_projection_zone(
        self,
        draw: ImageDraw.ImageDraw,
        bbox: Sequence[Any],
        *,
        label: str,
        color: ColorRGB,
        font: OverlayFont,
        confidence: float,
        primary: bool,
        bounds: Sequence[Any] | None = None,
    ) -> None:
        clipped = [int(round(float(value))) for value in bbox[:4]]
        radius = 14 if primary else 12
        fill_alpha = 28 if primary else 12
        outline_alpha = 228 if primary else 126
        glow_alpha = 42 if primary else 20
        draw.rounded_rectangle(clipped, radius=radius, fill=_rgba(color, fill_alpha), outline=_rgba(color, glow_alpha), width=7)
        draw.rounded_rectangle(clipped, radius=radius, outline=_rgba(color, outline_alpha), width=2)
        tag = f"{label} {confidence:.2f}" if primary else label
        tag_width, tag_height = self._overlay_tag_size(draw, tag, font=font, padding_x=10, padding_y=4)
        if bounds is None:
            bound_left, bound_top, bound_right, bound_bottom = 0, 0, max(clipped[2] + 1, 1), max(clipped[3] + 1, 1)
        else:
            bound_left, bound_top, bound_right, bound_bottom = [int(round(float(value))) for value in bounds[:4]]
        tag_x = max(bound_left + 4, min(int(clipped[0]) + 8, bound_right - tag_width - 4))
        tag_y = int(clipped[1]) - tag_height - 8
        if tag_y < bound_top + 4:
            tag_y = int(clipped[1]) + 8
        tag_y = max(bound_top + 4, min(tag_y, bound_bottom - tag_height - 4))
        self._draw_overlay_tag(
            draw,
            (tag_x, tag_y),
            tag,
            font=font,
            fill=(7, 16, 22, 224 if primary else 196),
            outline=_rgba(color, outline_alpha),
            text_fill=_rgba(color, 255 if primary else 210),
            padding_x=10,
            padding_y=4,
        )

    def _draw_dashed_line(
        self,
        draw: ImageDraw.ImageDraw,
        start: tuple[int, int],
        end: tuple[int, int],
        color: ColorRGBA,
        *,
        width: int = 2,
        dash: int = 8,
        gap: int = 6,
    ) -> None:
        start_x, start_y = start
        end_x, end_y = end
        delta_x = float(end_x - start_x)
        delta_y = float(end_y - start_y)
        distance = float((delta_x * delta_x + delta_y * delta_y) ** 0.5)
        if distance <= 1.0:
            return
        unit_x = delta_x / distance
        unit_y = delta_y / distance
        cursor = 0.0
        dash_length = max(1.0, float(dash))
        gap_length = max(1.0, float(gap))
        while cursor < distance:
            segment_end = min(distance, cursor + dash_length)
            x0 = start_x + unit_x * cursor
            y0 = start_y + unit_y * cursor
            x1 = start_x + unit_x * segment_end
            y1 = start_y + unit_y * segment_end
            draw.line((x0, y0, x1, y1), fill=color, width=max(1, int(width)))
            cursor += dash_length + gap_length

    def _structure_overlay_label(
        self,
        key: str,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> str:
        key_name = str(key or "").strip().lower()
        if key_name == "global":
            return f"GLOBAL {tracking_summary.get('global_direction', 'HOLD')}"
        if key_name == "local":
            return f"LOCAL {tracking_summary.get('local_direction', 'HOLD')}"
        if key_name == "current":
            action = str(latest_signal.get("action", "HOLD") or "HOLD").upper()
            return f"CURRENT {action}"
        return str(key or "WINDOW").upper()

    def _overlay_tag_size(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        *,
        font: OverlayFont,
        padding_x: int = 10,
        padding_y: int = 5,
    ) -> tuple[int, int]:
        bounds = draw.textbbox((0, 0), str(text or ""), font=font)
        width = max(0, int(bounds[2] - bounds[0])) + (padding_x * 2)
        height = max(0, int(bounds[3] - bounds[1])) + (padding_y * 2)
        return width, height

    def _draw_overlay_tag(
        self,
        draw: ImageDraw.ImageDraw,
        origin: tuple[int, int],
        text: str,
        *,
        font: OverlayFont,
        fill: tuple[int, int, int, int],
        outline: tuple[int, int, int, int],
        text_fill: tuple[int, int, int, int],
        padding_x: int = 10,
        padding_y: int = 5,
    ) -> tuple[int, int]:
        label = str(text or "").strip()
        if not label:
            return 0, 0
        bounds = draw.textbbox((0, 0), label, font=font)
        width = max(0, int(bounds[2] - bounds[0]))
        height = max(0, int(bounds[3] - bounds[1]))
        x = int(origin[0])
        y = int(origin[1])
        tag_box = [x, y, x + width + (padding_x * 2), y + height + (padding_y * 2)]
        radius = max(10, min(16, int(round((tag_box[3] - tag_box[1]) * 0.45))))
        draw.rounded_rectangle(tag_box, radius=radius, fill=fill, outline=outline, width=1)
        draw.text(
            (x + padding_x - int(bounds[0]), y + padding_y - int(bounds[1])),
            label,
            font=font,
            fill=text_fill,
        )
        return int(tag_box[2] - tag_box[0]), int(tag_box[3] - tag_box[1])

    def _layout_overlay_tags(
        self,
        draw: ImageDraw.ImageDraw,
        items: Sequence[Mapping[str, Any]],
        origin: tuple[int, int],
        *,
        max_width: int,
        font: OverlayFont,
        gap: int = 6,
        row_gap: int = 8,
    ) -> tuple[list[dict[str, Any]], int]:
        start_x = int(origin[0])
        start_y = int(origin[1])
        x = start_x
        y = start_y
        row_height = 0
        placements: list[dict[str, Any]] = []
        for item in items:
            label = str(item.get("label", "") or "").strip()
            if not label:
                continue
            tag_width, tag_height = self._overlay_tag_size(draw, label, font=font)
            if x > start_x and (x + tag_width) > (start_x + max(1, int(max_width))):
                x = start_x
                y += row_height + row_gap
                row_height = 0
            placements.append(
                {
                    "x": x,
                    "y": y,
                    "label": label,
                    "fill": tuple(item.get("fill", (17, 30, 40, 208))),
                    "outline": tuple(item.get("outline", (83, 103, 116, 184))),
                    "text_fill": tuple(item.get("text_fill", (230, 238, 242, 255))),
                }
            )
            x += tag_width + gap
            row_height = max(row_height, tag_height)
        total_height = 0 if not placements else int((y - start_y) + row_height)
        return placements, total_height

    def _draw_overlay_tag_group(
        self,
        draw: ImageDraw.ImageDraw,
        placements: Sequence[Mapping[str, Any]],
        *,
        font: OverlayFont,
    ) -> None:
        for item in placements:
            self._draw_overlay_tag(
                draw,
                (int(item.get("x", 0) or 0), int(item.get("y", 0) or 0)),
                str(item.get("label", "") or ""),
                font=font,
                fill=cast(tuple[int, int, int, int], item.get("fill", (17, 30, 40, 208))),
                outline=cast(tuple[int, int, int, int], item.get("outline", (83, 103, 116, 184))),
                text_fill=cast(tuple[int, int, int, int], item.get("text_fill", (230, 238, 242, 255))),
            )

    def _draw_structure_box(
        self,
        draw: ImageDraw.ImageDraw,
        bbox: Sequence[Any],
        *,
        chart_box: Sequence[Any],
        label: str,
        color: ColorRGB,
        font: OverlayFont,
        fill_alpha: int = 0,
    ) -> None:
        clipped = [int(round(float(value))) for value in bbox[:4]]
        width = max(1, int(clipped[2] - clipped[0]))
        height = max(1, int(clipped[3] - clipped[1]))
        radius = max(10, min(18, int(round(min(width, height) * 0.16))))
        draw.rounded_rectangle(
            clipped,
            radius=radius,
            outline=_rgba(color, 54),
            width=5,
        )
        draw.rounded_rectangle(
            clipped,
            radius=radius,
            fill=(_rgba(color, fill_alpha) if fill_alpha > 0 else None),
            outline=_rgba(color, 216),
            width=2,
        )
        tag_width, tag_height = self._overlay_tag_size(draw, label, font=font, padding_x=10, padding_y=4)
        label_x = max(int(chart_box[0]) + 8, min(int(clipped[0]) + 10, int(clipped[2]) - tag_width - 8))
        above_y = int(clipped[1]) - tag_height - 8
        inside_y = int(clipped[1]) + 8
        label_y = above_y if above_y >= (int(chart_box[1]) + 8) else min(inside_y, int(clipped[3]) - tag_height - 8)
        self._draw_overlay_tag(
            draw,
            (label_x, max(int(chart_box[1]) + 8, label_y)),
            label,
            font=font,
            fill=(7, 16, 22, 224),
            outline=_rgba(color, 162),
            text_fill=_rgba(color, 255),
            padding_x=10,
            padding_y=4,
        )

    def _draw_structure_micro_plan(
        self,
        draw: ImageDraw.ImageDraw,
        box: Mapping[str, Any],
        *,
        chart_box: Sequence[Any],
        offset: tuple[float, float],
        color: ColorRGB,
        font: OverlayFont,
        emphasized: bool = False,
    ) -> None:
        chart_bounds = [int(round(float(value))) for value in chart_box[:4]]
        offset_x = float(offset[0])
        offset_y = float(offset[1])
        direction = _upper_action(box.get("direction", "HOLD"))
        role_specs: tuple[tuple[str, str, ColorRGB], ...] = (
            ("sniper_window", "SNP", (94, 194, 255)),
            ("trigger_window", "TRG", (238, 190, 90)),
            ("target_window", "TGT", color),
        )
        for key, label, role_color in role_specs:
            raw_bbox = cast(Sequence[Any], box.get(key, []))
            if len(raw_bbox) < 4:
                continue
            bbox = _clip_bbox_to_bounds(chart_bounds, _translate_bbox(raw_bbox, offset_x=offset_x, offset_y=offset_y))
            if int(bbox[2] - bbox[0]) < 8 or int(bbox[3] - bbox[1]) < 3:
                continue
            draw.rounded_rectangle(
                bbox,
                radius=5,
                fill=_rgba(role_color, 30 if emphasized else 18),
                outline=_rgba(role_color, 174 if emphasized else 116),
                width=1,
            )
            if int(bbox[2] - bbox[0]) >= 34 and int(bbox[3] - bbox[1]) >= 12:
                draw.text((int(bbox[0]) + 4, int(bbox[1]) + 1), label, font=font, fill=_rgba(role_color, 232 if emphasized else 178))

        try:
            invalidation_y = int(round(float(box.get("invalidation_y", 0.0)) + offset_y))
        except (TypeError, ValueError):
            return
        raw_box = cast(Sequence[Any], box.get("bbox", []))
        if len(raw_box) < 4 or not (chart_bounds[1] <= invalidation_y <= chart_bounds[3]):
            return
        x0 = int(round(float(raw_box[0]) + offset_x))
        x1 = int(round(float(raw_box[2]) + offset_x))
        x0 = max(chart_bounds[0] + 4, min(chart_bounds[2] - 4, x0))
        x1 = max(x0 + 6, min(chart_bounds[2] - 4, x1))
        invalidation_color: ColorRGBA = (230, 104, 104, 126) if direction == "BUY" else (112, 194, 255, 126)
        self._draw_dashed_line(draw, (x0, invalidation_y), (x1, invalidation_y), invalidation_color, width=1, dash=7, gap=6)

    def _draw_regression_line(
        self,
        draw: ImageDraw.ImageDraw,
        candles: Sequence[Mapping[str, Any]],
        color: ColorRGBA,
        *,
        offset: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        if len(candles) < 2:
            return
        offset_x = float(offset[0])
        offset_y = float(offset[1])
        xs = np.asarray([float(candle.get("center_x", 0.0)) + offset_x for candle in candles], dtype=np.float32)
        ys = np.asarray([float(candle.get("center_y", 0.0)) + offset_y for candle in candles], dtype=np.float32)
        if xs.size < 2 or float(np.std(xs)) <= 1e-9:
            return
        slope, intercept = np.polyfit(xs, ys, 1)
        start_x = float(xs.min())
        end_x = float(xs.max())
        start_y = float(slope * start_x + intercept)
        end_y = float(slope * end_x + intercept)
        line_width = 3
        point_radius = 4
        draw.line((start_x, start_y, end_x, end_y), fill=(0, 0, 0, 88), width=line_width + 3)
        draw.line((start_x, start_y, end_x, end_y), fill=color, width=line_width)
        draw.ellipse(
            (
                end_x - point_radius,
                end_y - point_radius,
                end_x + point_radius,
                end_y + point_radius,
            ),
            fill=color,
            outline=(7, 16, 22, 220),
            width=1,
        )

    def _detect_timeframe_selector(self, image: Image.Image) -> dict[str, Any]:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        if arr.ndim != 3:
            return {}
        height, width = int(arr.shape[0]), int(arr.shape[1])
        if height < 80 or width < 120:
            return {}

        roi_x1 = min(width, max(112, int(round(width * 0.42))))
        roi_y1 = min(height, max(56, int(round(height * 0.24))))
        roi = arr[:roi_y1, :roi_x1]
        candidates: list[dict[str, Any]] = []
        min_confidence_by_label: dict[str, float] = {
            "M1": 0.56,
            "M3": 0.56,
            "M5": 0.56,
            "M15": 0.60,
            "M30": 0.68,
            "H1": 0.68,
            "H4": 0.70,
            "D1": 0.72,
        }

        try:
            import cv2  # type: ignore[import-not-found]

            hsv = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)
            blue_mask = np.where(
                (hsv[:, :, 0] >= 88)
                & (hsv[:, :, 0] <= 132)
                & (hsv[:, :, 1] >= 70)
                & (hsv[:, :, 2] >= 70),
                255,
                0,
            ).astype(np.uint8)
            kernel = np.ones((3, 3), dtype=np.uint8)
            blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _hier = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            anchor_x = float(roi.shape[1]) * 0.38
            anchor_y = float(roi.shape[0]) * 0.55
            for contour in contours:
                x, y, box_w, box_h = cv2.boundingRect(contour)
                area = int(box_w * box_h)
                if area < 90 or box_w < 16 or box_h < 12:
                    continue
                if box_w > max(76, int(roi.shape[1] * 0.19)) or box_h > max(42, int(roi.shape[0] * 0.42)):
                    continue
                aspect = float(box_w) / float(max(box_h, 1))
                if aspect < 0.60 or aspect > 3.30:
                    continue
                sub_mask = blue_mask[y: y + box_h, x: x + box_w]
                blue_density = float(np.mean(sub_mask > 0))
                if blue_density < 0.28:
                    continue
                pad = max(2, int(round(box_h * 0.22)))
                crop = Image.fromarray(
                    roi[max(0, y - pad): min(roi.shape[0], y + box_h + pad), max(0, x - pad): min(roi.shape[1], x + box_w + pad)],
                    mode="RGB",
                )
                text_mask = self._extract_timeframe_text_mask(crop)
                label, template_score = self._score_timeframe_label(text_mask)
                if not label:
                    continue
                center_x = x + box_w * 0.5
                center_y = y + box_h * 0.5
                position_score = _clip01(
                    1.0
                    - (
                        (abs(center_x - anchor_x) / max(roi.shape[1], 1))
                        + (abs(center_y - anchor_y) / max(roi.shape[0], 1))
                    )
                    * 2.40
                )
                if position_score < 0.42:
                    continue
                score = _clip01(0.66 * template_score + 0.24 * position_score + 0.10 * blue_density)
                if score < float(min_confidence_by_label.get(label, 0.60)):
                    continue
                candidates.append(
                    {
                        "value": label,
                        "confidence": score,
                        "bbox": [int(x), int(y), int(x + box_w), int(y + box_h)],
                        "source": "selector_chip",
                    }
                )
        except Exception:
            return {}

        if not candidates:
            return {}
        candidates.sort(key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        return dict(candidates[0])

    def _extract_timeframe_text_mask(self, candidate_image: Image.Image) -> ArrayND:
        arr = np.asarray(candidate_image.convert("RGB"), dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[0] < 8 or arr.shape[1] < 8:
            return np.zeros((1, 1), dtype=np.uint8)
        try:
            import cv2  # type: ignore[import-not-found]

            hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            white_mask = np.where(
                ((hsv[:, :, 1] <= 104) & (hsv[:, :, 2] >= 176))
                | (gray >= 188),
                255,
                0,
            ).astype(np.uint8)
            kernel = np.ones((2, 2), dtype=np.uint8)
            white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel, iterations=1)
            white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        except Exception:
            gray = np.asarray(candidate_image.convert("L"), dtype=np.uint8)
            white_mask = np.where(gray >= 188, 255, 0).astype(np.uint8)

        bbox = _binary_content_bbox(white_mask)
        if bbox is None:
            return np.zeros((1, 1), dtype=np.uint8)
        x0, y0, x1, y1 = bbox
        cropped = white_mask[max(0, y0 - 1): y1 + 1, max(0, x0 - 1): x1 + 1]
        return (cropped > 0).astype(np.uint8)

    def _score_timeframe_label(self, text_mask: ArrayND) -> tuple[str, float]:
        if text_mask.ndim != 2 or text_mask.size == 0:
            return "", 0.0
        try:
            import cv2  # type: ignore[import-not-found]
        except Exception:
            return "", 0.0

        if int(np.sum(text_mask > 0)) < 14:
            return "", 0.0

        best_label = ""
        best_score = 0.0
        second_best = 0.0
        for label, templates in self._timeframe_template_bank.items():
            label_best = 0.0
            for template in templates:
                if template.size == 0:
                    continue
                resized = cv2.resize(
                    text_mask.astype(np.uint8),
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
                score = 0.58 * harmonic + 0.42 * iou
                if score > label_best:
                    label_best = score
            if label_best > best_score:
                second_best = best_score
                best_label = label
                best_score = label_best
            elif label_best > second_best:
                second_best = label_best
        margin = max(0.0, best_score - second_best)
        confidence = _clip01(0.76 * best_score + 0.34 * margin)
        return best_label, confidence


@dataclass(slots=True)
class _WorkerControl:
    thread: threading.Thread
    stop_evt: threading.Event
    capture_now_evt: threading.Event


class ContinuousWindowTrackerService:
    def __init__(
        self,
        *,
        observer_service: SignalObserverService | None = None,
        root_dir: Path | None = None,
        capture_backend: WindowCaptureBackend | None = None,
        tracking_adapter: WindowTrackingAdapter | None = None,
        focus_selector_backend: FocusSelectionBackend | None = None,
        execution_backend: BrokerExecutionBackend | None = None,
    ) -> None:
        self.observer_service = observer_service
        self.root_dir = Path(root_dir or (RUNTIME.data_dir / "window_tracker"))
        self.sessions_dir = self.root_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.capture_backend = capture_backend or WindowsWindowCaptureBackend()
        self.tracking_adapter = tracking_adapter or PhoenixGuardWindowTrackingAdapter()
        self.focus_selector_backend = focus_selector_backend or WindowsNativeFocusSelectionBackend()
        self.execution_backend = execution_backend or PocketOptionBrokerExecutionBackend()
        self._lock = threading.RLock()
        self._workers: dict[str, _WorkerControl] = {}
        self._next_capture_epoch: dict[str, float] = {}
        self._last_capture_time: dict[str, float] = {}
        self._capture_rate_limit_sec = 0.2
        self._emergency_hotkey_stop_evt = threading.Event()
        self._emergency_hotkey_thread: threading.Thread | None = None
        self._last_emergency_hotkey_down = False
        self._start_emergency_hotkey_listener()

    def _artifact_path_if_exists(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        path = Path(raw)
        return str(path) if path.exists() else ""

    def _focus_locked_tracking_summary(self) -> dict[str, Any]:
        return _default_tracking_summary(
            message="Focus locked. Capture now or start tracker to generate the live overlay."
        )

    def _focus_locked_signal(self, *, status: str = "ready") -> dict[str, Any]:
        return _default_signal(
            message="Focus locked. Capture now or start tracker to generate the live overlay.",
            status=status,
        )

    def _event_log_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    def _write_session_event_log(self, session_id: str, event: str, **fields: Any) -> None:
        try:
            path = self._event_log_path(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "timestamp": _now_iso(),
                "session_id": str(session_id),
                "event": str(event or "event"),
                **fields,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        except Exception:
            LOGGER.debug("Unable to append tracker event log for %s.", session_id, exc_info=True)

    def _prune_session_artifacts(self, artifact_dir: Path) -> None:
        keep_frames = max(24, int(_TRACKER_ARTIFACT_RETENTION_FRAMES))
        try:
            if not artifact_dir.exists():
                return
            frame_groups: dict[str, list[Path]] = {}
            for path in artifact_dir.iterdir():
                if not path.is_file():
                    continue
                parts = path.name.split("_", 2)
                if len(parts) < 3 or not parts[0].isdigit():
                    continue
                frame_groups.setdefault(f"{parts[0]}_{parts[1]}", []).append(path)
            if len(frame_groups) <= keep_frames:
                return
            ordered = sorted(
                frame_groups.items(),
                key=lambda item: int(item[0].split("_", 1)[0]),
                reverse=True,
            )
            stale_groups = ordered[keep_frames:]
            removed = 0
            for _, paths in stale_groups:
                for path in paths:
                    try:
                        path.unlink(missing_ok=True)
                        removed += 1
                    except FileNotFoundError:
                        continue
                    except Exception:
                        LOGGER.debug("Unable to prune tracker artifact %s.", path, exc_info=True)
            if removed:
                LOGGER.info(
                    "Pruned %s stale tracker artifacts from %s; retained %s frame groups.",
                    removed,
                    artifact_dir,
                    keep_frames,
                )
        except Exception:
            LOGGER.debug("Tracker artifact pruning failed for %s.", artifact_dir, exc_info=True)

    def _start_emergency_hotkey_listener(self) -> None:
        try:
            import os

            if os.name != "nt" or str(os.getenv("PHOENIXGUARD_DISABLE_TRACKER_STOP_HOTKEY", "")).strip() == "1":
                return
        except Exception:
            return
        if self._emergency_hotkey_thread is not None and self._emergency_hotkey_thread.is_alive():
            return
        self._emergency_hotkey_thread = threading.Thread(
            target=self._emergency_hotkey_loop,
            name="window-tracker-emergency-stop-hotkey",
            daemon=True,
        )
        self._emergency_hotkey_thread.start()

    def _emergency_hotkey_loop(self) -> None:
        try:
            import ctypes

            user32 = ctypes.windll.user32
        except Exception:
            return
        while not self._emergency_hotkey_stop_evt.is_set():
            try:
                ctrl_down = bool(
                    (int(user32.GetAsyncKeyState(0x11)) & 0x8000)
                    or (int(user32.GetAsyncKeyState(0xA2)) & 0x8000)
                    or (int(user32.GetAsyncKeyState(0xA3)) & 0x8000)
                )
                alt_down = bool(
                    (int(user32.GetAsyncKeyState(0x12)) & 0x8000)
                    or (int(user32.GetAsyncKeyState(0xA4)) & 0x8000)
                    or (int(user32.GetAsyncKeyState(0xA5)) & 0x8000)
                )
                end_down = bool(int(user32.GetAsyncKeyState(0x23)) & 0x8000)
                combo_down = bool(ctrl_down and alt_down and end_down)
                if combo_down and not self._last_emergency_hotkey_down:
                    LOGGER.warning("PhoenixGuard tracker emergency stop hotkey pressed.")
                    self.emergency_stop_all(reason="Emergency stop hotkey Ctrl+Alt+End was pressed.")
                self._last_emergency_hotkey_down = combo_down
            except Exception:
                LOGGER.debug("Tracker emergency hotkey loop iteration failed.", exc_info=True)
            time.sleep(0.08)

    def _normalized_session_memory_projection(
        self,
        payload: Mapping[str, Any],
        *,
        mode: str,
    ) -> dict[str, Any]:
        frame_index = int(payload.get("frame_index", 0) or 0)
        chart_path = self._artifact_path_if_exists(payload.get("last_chart_path", ""))
        normalized = _normalize_memory_projection_payload(payload.get(f"memory_projection_{mode}", {}), mode=mode)
        normalized["source_chart_path"] = self._artifact_path_if_exists(normalized.get("source_chart_path", ""))
        normalized["reference_image_path"] = self._artifact_path_if_exists(normalized.get("reference_image_path", ""))
        normalized["projection_image_path"] = self._artifact_path_if_exists(normalized.get("projection_image_path", ""))
        return _mark_memory_projection_payload_stale(
            normalized,
            mode=mode,
            frame_index=frame_index,
            chart_path=chart_path,
        )

    def _normalize_session_payload(self, payload: Mapping[str, Any], *, session_id_hint: str = "") -> dict[str, Any]:
        raw = dict(payload)
        session_id = _slugify(
            str(raw.get("session_id", "") or session_id_hint or "").strip(),
            _slugify(session_id_hint or f"tracker-{uuid4().hex[:10]}", "tracker-session"),
        )
        manual_focus_region = _public_manual_focus_region(raw.get("manual_focus_region", {}))
        locked_window = _mapping_to_dict(raw.get("locked_window", {}))
        if not locked_window and int(raw.get("locked_hwnd", 0) or 0) > 0:
            locked_window = {
                "hwnd": int(raw.get("locked_hwnd", 0) or 0),
                "title": str(raw.get("locked_title", "") or ""),
            }
        locked_title = str(
            raw.get("locked_title", "") or locked_window.get("title", "") or ""
        )
        focus_selector = _public_focus_selector_state(
            raw.get("focus_selector", {}),
            supported=self.focus_selector_backend.is_supported(),
        )
        execution_controls = _normalize_execution_controls(raw.get("execution_controls", {}))
        broker_surface = dict(_default_broker_surface_payload(), **_mapping_to_dict(raw.get("broker_surface", {})))
        broker_execution_state = _normalize_broker_execution_state(raw.get("broker_execution_state", {}))
        if bool(manual_focus_region.get("enabled", False)) and focus_selector.get("status") in {"idle", "cancelled"}:
            focus_selector = _focus_selector_state(
                supported=self.focus_selector_backend.is_supported(),
                armed=False,
                active=False,
                status="selected",
                message="Broker focus locked. The tracker will only study this selected surface.",
            )
        if not bool(manual_focus_region.get("enabled", False)) and focus_selector.get("status") not in {"armed", "selecting", "error"}:
            focus_selector = _focus_selector_state(
                supported=self.focus_selector_backend.is_supported(),
                message=_focus_required_message(),
            )

        tracking_enabled = bool(raw.get("tracking_enabled", False))
        if tracking_enabled:
            status = "running"
        elif bool(manual_focus_region.get("enabled", False)):
            status = "ready"
        else:
            status = "awaiting_focus"

        last_window_path = self._artifact_path_if_exists(
            raw.get("last_window_path", raw.get("last_frame_path", ""))
        )
        last_frame_path = self._artifact_path_if_exists(
            raw.get("last_frame_path", raw.get("last_window_path", ""))
        )
        last_chart_path = self._artifact_path_if_exists(raw.get("last_chart_path", ""))
        last_overlay_path = self._artifact_path_if_exists(
            raw.get(
                "last_overlay_path",
                raw.get("last_tracker_overlay_path", raw.get("last_display_chart_path", "")),
            )
        )
        last_display_chart_path = self._artifact_path_if_exists(
            raw.get("last_display_chart_path", last_overlay_path or last_chart_path)
        )
        last_decision_path = self._artifact_path_if_exists(raw.get("last_decision_path", ""))
        frame_index = int(raw.get("frame_index", 0) or 0)
        predict_projection = _mark_memory_projection_payload_stale(
            raw.get("memory_projection_predict", {}),
            mode="predict",
            frame_index=frame_index,
            chart_path=last_chart_path,
        )
        future_projection = _mark_memory_projection_payload_stale(
            raw.get("memory_projection_future", {}),
            mode="future",
            frame_index=frame_index,
            chart_path=last_chart_path,
        )
        active_memory_projection_mode = str(raw.get("memory_projection_active_mode", "") or "").strip().lower()
        if active_memory_projection_mode not in {"predict", "future"}:
            active_memory_projection_mode = ""
        recent_studies = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], raw.get("recent_studies", []))
            if isinstance(item, Mapping)
        ]

        has_preview_artifacts = bool(last_chart_path or last_overlay_path)
        tracking_summary = _mapping_to_dict(raw.get("tracking_summary", {}))
        latest_signal = _mapping_to_dict(raw.get("latest_signal", {}))
        if not bool(manual_focus_region.get("enabled", False)):
            tracking_summary = _default_tracking_summary(message="Awaiting locked broker focus.")
            latest_signal = _default_signal(
                message="Awaiting locked broker focus before live tracking can start.",
                status="awaiting_focus",
            )
        elif not has_preview_artifacts:
            tracking_summary = (
                _default_tracking_summary(message="Tracker warming on the locked broker surface.")
                if tracking_enabled
                else self._focus_locked_tracking_summary()
            )
            latest_signal = (
                _default_signal(
                    message="Tracker warming on the locked broker surface.",
                    status="warming",
                )
                if tracking_enabled
                else self._focus_locked_signal(status="ready")
            )
        else:
            latest_signal.setdefault("status", "tracking" if tracking_enabled else "ready")

        normalized: dict[str, Any] = {
            "session_id": session_id,
            "name": str(raw.get("name", "") or session_id),
            "market": str(raw.get("market", "") or "").strip().upper(),
            "window_query": str(raw.get("window_query", "Pocket Option") or "Pocket Option").strip() or "Pocket Option",
            "layout_profile": str(raw.get("layout_profile", "manual_focus_only") or "manual_focus_only"),
            "effective_layout_profile": "manual_focus_only",
            "capture_interval_sec": max(
                _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC,
                float(raw.get("capture_interval_sec", _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC) or _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC),
            ),
            "rl_track_interval_sec": max(0.05, float(raw.get("rl_track_interval_sec", 30.0) or 30.0)),
            "observer_settings": dict(_mapping_to_dict(raw.get("observer_settings", {}))),
            "observer_policy": dict(_mapping_to_dict(raw.get("observer_policy", {}))),
            "status": status,
            "tracking_enabled": tracking_enabled,
            "created_at": str(raw.get("created_at", "") or _now_iso()),
            "updated_at": str(raw.get("updated_at", "") or _now_iso()),
            "last_capture_at": str(raw.get("last_capture_at", "") or ""),
            "last_error": str(raw.get("last_error", "") or ""),
            "capture_count": int(raw.get("capture_count", 0) or 0),
            "frame_index": int(raw.get("frame_index", 0) or 0),
            "locked_window": locked_window,
            "locked_title": locked_title,
            "manual_focus_region": {
                "enabled": bool(manual_focus_region.get("enabled", False)),
                "normalized_bbox": list(cast(Sequence[float], manual_focus_region.get("normalized_bbox", []))),
                "source": str(manual_focus_region.get("source", "") or ""),
                "updated_at": str(manual_focus_region.get("updated_at", "") or ""),
            },
            "focus_selector": focus_selector,
            "execution_controls": execution_controls,
            "broker_surface": broker_surface,
            "broker_execution_state": broker_execution_state,
            "scenario_analysis": _mapping_to_dict(raw.get("scenario_analysis", {})),
            "tracking_summary": tracking_summary,
            "latest_signal": latest_signal,
            "recent_studies": recent_studies,
            "last_frame_path": last_frame_path,
            "last_window_path": last_window_path,
            "last_chart_path": last_chart_path,
            "last_full_overlay_path": self._artifact_path_if_exists(raw.get("last_full_overlay_path", "")),
            "last_display_chart_path": last_display_chart_path,
            "last_overlay_path": last_overlay_path,
            "last_decision_path": last_decision_path,
            "memory_projection_predict": predict_projection,
            "memory_projection_future": future_projection,
            "memory_projection_active_mode": active_memory_projection_mode,
        }
        return normalized

    def _ensure_preview_for_locked_focus(self, session_id: str, payload: Mapping[str, Any]) -> None:
        manual_focus = _public_manual_focus_region(payload.get("manual_focus_region", {}))
        if not bool(manual_focus.get("enabled", False)):
            return
        if bool(payload.get("tracking_enabled", False)):
            return
        if str(payload.get("last_chart_path", "") or "").strip():
            return
        if str(payload.get("last_overlay_path", "") or "").strip():
            return
        self._capture_and_analyze(session_id, force=True)

    def list_windows(self, query: str | None = None) -> list[dict[str, Any]]:
        return self.capture_backend.list_windows(query)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for session_path in sorted(self.sessions_dir.glob("*/session.json"), reverse=True):
            payload = self._load_session(session_path.parent.name)
            if payload:
                payloads.append(self._public_session_payload(payload))
        payloads.sort(key=lambda item: str(item.get("updated_at", "") or ""), reverse=True)
        return payloads[: max(1, int(limit))]

    def create_session(
        self,
        *,
        session_id: str | None = None,
        name: str = "",
        market: str = "",
        window_query: str = "Pocket Option",
        layout_profile: str = "manual_focus_only",
        capture_interval_sec: float = _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC,
        rl_track_interval_sec: float = 30.0,
        auto_start: bool = False,
        observer_settings: Mapping[str, Any] | None = None,
        observer_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_session_id = _slugify(str(session_id or "").strip(), f"tracker-{uuid4().hex[:10]}")
        with self._lock:
            existing = self._load_session(normalized_session_id)
            if existing:
                return self._public_session_payload(existing)

            payload: dict[str, Any] = {
                "session_id": normalized_session_id,
                "name": str(name or normalized_session_id),
                "market": str(market or "").strip().upper(),
                "window_query": str(window_query or "Pocket Option").strip() or "Pocket Option",
                "layout_profile": str(layout_profile or "manual_focus_only"),
                "effective_layout_profile": "manual_focus_only",
                "capture_interval_sec": max(_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC, float(capture_interval_sec or _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC)),
                "rl_track_interval_sec": max(0.05, float(rl_track_interval_sec or 30.0)),
                "observer_settings": dict(observer_settings or {}),
                "observer_policy": dict(observer_policy or {}),
                "status": "awaiting_focus",
                "tracking_enabled": False,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "last_capture_at": "",
                "last_error": "",
                "capture_count": 0,
                "frame_index": 0,
                "locked_window": {},
                "locked_title": "",
                "manual_focus_region": {
                    "enabled": False,
                    "normalized_bbox": [],
                    "source": "",
                    "updated_at": "",
                },
                "focus_selector": _focus_selector_state(
                    supported=self.focus_selector_backend.is_supported(),
                    message=_focus_required_message(),
                ),
                "execution_controls": _default_execution_controls(),
                "broker_surface": _default_broker_surface_payload(),
                "broker_execution_state": _default_broker_execution_state(),
                "scenario_analysis": {
                    "enabled": False,
                    "status": "disabled",
                    "summary": "A* scenario generation is disabled.",
                    "generated_at": _now_iso(),
                    "total_scenarios": 0,
                    "top_scenario": {},
                    "scenarios": [],
                    "overlay": {"confidence_heatmap": [], "tree_structure": {}},
                },
                "tracking_summary": _default_tracking_summary(message="Awaiting locked broker focus."),
                "latest_signal": _default_signal(
                    message="Awaiting locked broker focus before live tracking can start.",
                    status="awaiting_focus",
                ),
                "recent_studies": [],
                "last_frame_path": "",
                "last_window_path": "",
                "last_chart_path": "",
                "last_display_chart_path": "",
                "last_overlay_path": "",
                "last_decision_path": "",
                "memory_projection_predict": _default_memory_projection_payload(mode="predict"),
                "memory_projection_future": _default_memory_projection_payload(mode="future"),
                "memory_projection_active_mode": "",
            }
            self._save_session(payload)
        if auto_start:
            return self.start_session(normalized_session_id)
        return self.get_session(normalized_session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        payload = self._require_session(session_id)
        if bool(payload.get("tracking_enabled", False)):
            self._ensure_worker(str(payload["session_id"]))
        else:
            self._ensure_preview_for_locked_focus(str(payload["session_id"]), payload)
            payload = self._require_session(session_id)
        return self._public_session_payload(payload)

    def load_session_payload(self, session_id: str) -> dict[str, Any]:
        return self._load_session(session_id)

    def resolve_window_descriptor(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._resolve_window_descriptor(payload)

    def session_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id)

    def set_focus_region(
        self,
        session_id: str,
        normalized_bbox: Sequence[Any],
        *,
        source: str = "dashboard_ctrl_v",
    ) -> dict[str, Any]:
        payload = self._require_session(session_id)
        try:
            validated_bbox = _normalize_focus_region_bbox(normalized_bbox)
        except ValueError as exc:
            LOGGER.error("Invalid focus region bbox for session %s: %s", session_id, exc)
            raise
        
        focus_region = {
            "enabled": True,
            "normalized_bbox": validated_bbox,
            "source": str(source or "dashboard_ctrl_v"),
            "updated_at": _now_iso(),
        }
        
        with self._lock:
            payload["manual_focus_region"] = focus_region
            payload["focus_selector"] = _focus_selector_state(
                supported=self.focus_selector_backend.is_supported(),
                armed=False,
                active=False,
                status="selected",
                message="Broker focus locked. The tracker will only study this selected surface.",
                target_hwnd=int(payload.get("locked_window", {}).get("hwnd", 0) or 0),
                target_title=str(payload.get("locked_title", "") or ""),
            )
            payload["status"] = "ready"
            payload["updated_at"] = _now_iso()
            payload["last_error"] = ""
            payload["tracking_summary"] = self._focus_locked_tracking_summary()
            payload["latest_signal"] = self._focus_locked_signal(status="ready")
            payload["last_chart_path"] = ""
            payload["last_full_overlay_path"] = ""
            payload["last_display_chart_path"] = ""
            payload["last_overlay_path"] = ""
            payload["last_decision_path"] = ""
            payload["memory_projection_predict"] = _default_memory_projection_payload(mode="predict")
            payload["memory_projection_future"] = _default_memory_projection_payload(mode="future")
            payload["memory_projection_active_mode"] = ""
            self._save_session(payload)
            worker = self._workers.get(str(payload["session_id"]))
            if worker is not None:
                worker.capture_now_evt.set()
        
        LOGGER.info("Focus region set for session %s: bbox=%s from %s", session_id, validated_bbox, source)
        
        if worker is None:
            self._capture_and_analyze(str(payload["session_id"]), force=True)
        
        return self.get_session(str(payload["session_id"]))

    def clear_focus_region(self, session_id: str) -> dict[str, Any]:
        payload = self._require_session(session_id)
        self._stop_worker(str(payload["session_id"]))
        with self._lock:
            payload["tracking_enabled"] = False
            payload["status"] = "awaiting_focus"
            payload["manual_focus_region"] = {
                "enabled": False,
                "normalized_bbox": [],
                "source": "",
                "updated_at": _now_iso(),
            }
            payload["focus_selector"] = _focus_selector_state(
                supported=self.focus_selector_backend.is_supported(),
                message=_focus_required_message(),
            )
            payload["broker_surface"] = _default_broker_surface_payload(message="Focus was cleared.")
            payload["broker_execution_state"] = _default_broker_execution_state(
                status="disabled",
                message="Focus was cleared. Live execution is disabled until a broker surface is locked.",
            )
            payload["tracking_summary"] = _default_tracking_summary(message="Focus was cleared. Lock a new broker surface.")
            payload["latest_signal"] = _default_signal(
                message="Focus was cleared. Lock a new broker surface before tracking.",
                status="awaiting_focus",
            )
            payload["last_chart_path"] = ""
            payload["last_full_overlay_path"] = ""
            payload["last_display_chart_path"] = ""
            payload["last_overlay_path"] = ""
            payload["last_decision_path"] = ""
            payload["memory_projection_predict"] = _default_memory_projection_payload(mode="predict")
            payload["memory_projection_future"] = _default_memory_projection_payload(mode="future")
            payload["memory_projection_active_mode"] = ""
            payload["updated_at"] = _now_iso()
            self._save_session(payload)
        return self.get_session(str(payload["session_id"]))

    def arm_focus_selector(self, session_id: str) -> dict[str, Any]:
        payload = self._require_session(session_id)
        descriptor = self._resolve_window_descriptor(payload)
        
        if descriptor is None:
            window_query = str(payload.get('window_query', 'Pocket Option') or 'Pocket Option')
            error_msg = (
                f"Cannot arm focus selector: No window matched '{window_query}'. "
                f"Make sure Pocket Option is open in a browser window."
            )
            LOGGER.warning(error_msg)
            with self._lock:
                payload["focus_selector"] = _focus_selector_state(
                    supported=self.focus_selector_backend.is_supported(),
                    armed=False,
                    active=False,
                    status="error",
                    message=error_msg,
                    last_error=error_msg,
                )
                payload["status"] = "waiting_for_window"
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            return self.get_session(str(payload["session_id"]))
        
        if not self.focus_selector_backend.is_supported():
            error_msg = (
                "Cannot arm focus selector: Native broker focus selection is not supported on this platform. "
                "PhoenixGuard focus selector only works on Windows."
            )
            LOGGER.error(error_msg)
            with self._lock:
                payload["focus_selector"] = _focus_selector_state(
                    supported=False,
                    status="error",
                    message=error_msg,
                    last_error=error_msg,
                )
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            return self.get_session(str(payload["session_id"]))

        try:
            LOGGER.info(
                f"Arming focus selector for session {session_id}: "
                f"hwnd={descriptor.get('hwnd')} title={descriptor.get('title', 'Unknown')[:40]}"
            )
            
            self.focus_selector_backend.arm_selection(
                session_id=str(payload["session_id"]),
                descriptor=descriptor,
                on_selected=self._on_focus_selected,
                on_state_change=self._on_focus_state_change,
            )
            
            with self._lock:
                payload["locked_window"] = dict(descriptor)
                payload["locked_title"] = str(descriptor.get("title", "") or "")
                payload["focus_selector"] = _focus_selector_state(
                    supported=True,
                    armed=True,
                    active=False,
                    status="armed",
                    message=(
                        "Focus selector armed! Switch to Pocket Option, press Ctrl+V, "
                        "drag the chart surface, then press Enter."
                    ),
                    target_hwnd=int(descriptor.get("hwnd", 0) or 0),
                    target_title=str(descriptor.get("title", "") or ""),
                )
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            
            return self.get_session(str(payload["session_id"]))
            
        except Exception as e:
            error_msg = f"Failed to arm focus selector: {str(e)[:100]}"
            LOGGER.error(error_msg, exc_info=True)
            with self._lock:
                payload["focus_selector"] = _focus_selector_state(
                    supported=self.focus_selector_backend.is_supported(),
                    armed=False,
                    active=False,
                    status="error",
                    message=error_msg,
                    last_error=error_msg,
                    target_hwnd=int(descriptor.get("hwnd", 0) or 0),
                    target_title=str(descriptor.get("title", "") or ""),
                )
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            return self.get_session(str(payload["session_id"]))

    def cancel_focus_selector(self, session_id: str) -> dict[str, Any]:
        payload = self._require_session(session_id)
        self.focus_selector_backend.cancel_selection(session_id=str(payload["session_id"]))
        with self._lock:
            payload["focus_selector"] = _focus_selector_state(
                supported=self.focus_selector_backend.is_supported(),
                armed=False,
                active=False,
                status="idle",
                message=_focus_required_message(),
            )
            payload["updated_at"] = _now_iso()
            self._save_session(payload)
        return self.get_session(str(payload["session_id"]))

    def latest_artifact_path(self, session_id: str, artifact_kind: str) -> Path:
        payload = self._require_session(session_id)
        kind = str(artifact_kind or "").strip().lower()
        active_mode = str(payload.get("memory_projection_active_mode", "") or "").strip().lower()
        active_projection = self._normalized_session_memory_projection(
            payload,
            mode="future" if active_mode == "future" else "predict",
        )
        candidates = {
            "window": str(payload.get("last_window_path", "") or ""),
            "frame": str(payload.get("last_frame_path", "") or ""),
            "chart": str(payload.get("last_chart_path", "") or ""),
            "display-chart": str(payload.get("last_display_chart_path", "") or ""),
            "overlay": str(payload.get("last_overlay_path", "") or ""),
            "full-overlay": str(payload.get("last_full_overlay_path", "") or ""),
            "decision": str(payload.get("last_decision_path", "") or ""),
            "memory-reference": str(active_projection.get("reference_image_path", "") or ""),
            "projection": str(active_projection.get("projection_image_path", "") or ""),
        }
        raw_path = candidates.get(kind, "")
        if not raw_path:
            raise FileNotFoundError(kind)
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(kind)
        return path

    def start_session(self, session_id: str) -> dict[str, Any]:
        payload = self._require_session(session_id)
        manual_focus = _public_manual_focus_region(payload.get("manual_focus_region", {}))
        if not bool(manual_focus.get("enabled", False)):
            with self._lock:
                payload["tracking_enabled"] = False
                payload["status"] = "awaiting_focus"
                payload["latest_signal"] = _default_signal(
                    message="Tracker is waiting for a locked broker focus before it can run.",
                    status="awaiting_focus",
                )
                payload["tracking_summary"] = _default_tracking_summary(message="Awaiting locked broker focus.")
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            return self.get_session(str(payload["session_id"]))
        with self._lock:
            payload["tracking_enabled"] = True
            payload["status"] = "running"
            payload["updated_at"] = _now_iso()
            payload["last_error"] = ""
            self._save_session(payload)
            self._write_session_event_log(
                str(payload["session_id"]),
                "tracker_started",
                status="running",
                capture_interval_sec=float(payload.get("capture_interval_sec", _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC) or _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC),
            )
        self._ensure_worker(str(payload["session_id"]), capture_now=True)
        return self.get_session(str(payload["session_id"]))

    def stop_session(self, session_id: str) -> dict[str, Any]:
        payload = self._require_session(session_id)
        self._stop_worker(str(payload["session_id"]))
        with self._lock:
            payload["tracking_enabled"] = False
            payload["status"] = "ready" if _public_manual_focus_region(payload.get("manual_focus_region", {})).get("enabled", False) else "awaiting_focus"
            payload["updated_at"] = _now_iso()
            self._save_session(payload)
            self._write_session_event_log(
                str(payload["session_id"]),
                "tracker_stopped",
                status=str(payload["status"]),
                reason="manual_stop",
            )
        return self.get_session(str(payload["session_id"]))

    def emergency_stop_session(self, session_id: str, *, reason: str = "Emergency stop requested.") -> dict[str, Any]:
        payload = self._require_session(session_id)
        normalized_session_id = str(payload["session_id"])
        self._stop_worker(normalized_session_id)
        with self._lock:
            payload = self._load_session(normalized_session_id) or payload
            controls = _normalize_execution_controls(payload.get("execution_controls", {}))
            controls["live_execution_enabled"] = False
            controls["execution_mode"] = "shadow"
            state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
            state["enabled"] = False
            state["mode"] = "shadow"
            state["status"] = "emergency_stop"
            state["message"] = str(reason or "Emergency stop requested.")
            state["side"] = "HOLD"
            state["lane"] = "EMERGENCY_STOP"
            state["actionable"] = False
            state["recent_log"] = self._append_execution_log(
                state,
                status="emergency_stop",
                message=state["message"],
                side="HOLD",
                lane="EMERGENCY_STOP",
            )
            payload["tracking_enabled"] = False
            payload["status"] = "ready" if _public_manual_focus_region(payload.get("manual_focus_region", {})).get("enabled", False) else "awaiting_focus"
            payload["execution_controls"] = _normalize_execution_controls(controls)
            payload["broker_execution_state"] = state
            latest_signal = _mapping_to_dict(payload.get("latest_signal", {}))
            latest_signal["execution_action"] = "HOLD"
            latest_signal["broker_execution_state"] = state
            payload["latest_signal"] = latest_signal
            payload["updated_at"] = _now_iso()
            self._save_session(payload)
            self._write_session_event_log(
                normalized_session_id,
                "emergency_stop",
                status=str(payload["status"]),
                message=state["message"],
            )
        return self.get_session(normalized_session_id)

    def emergency_stop_all(self, *, reason: str = "Emergency stop requested.") -> list[dict[str, Any]]:
        session_ids = {
            str(path.parent.name)
            for path in self.sessions_dir.glob("*/session.json")
        }
        session_ids.update(self._workers.keys())
        stopped: list[dict[str, Any]] = []
        for session_id in sorted(session_ids):
            try:
                stopped.append(self.emergency_stop_session(session_id, reason=reason))
            except Exception:
                LOGGER.exception("Emergency stop failed for tracker session %s.", session_id)
        return stopped

    def capture_once(self, session_id: str) -> dict[str, Any]:
        payload = self._require_session(session_id)
        self._capture_and_analyze(str(payload["session_id"]), force=True)
        return self.get_session(str(payload["session_id"]))

    def execute_demo_random_trade(
        self,
        session_id: str,
        *,
        side: str | None = None,
        expiry_seconds: int = 180,
    ) -> dict[str, Any]:
        payload = self._require_session(session_id)
        manual_focus = _public_manual_focus_region(payload.get("manual_focus_region", {}))
        if not bool(manual_focus.get("enabled", False)):
            raise ValueError("Lock broker focus before running a demo execution test.")

        selected_side = _upper_action(side, fallback="HOLD")
        if selected_side not in {"BUY", "SELL"}:
            selected_side = str(secrets.choice(("BUY", "SELL")))
        selected_expiry = max(_EXECUTION_MIN_LIVE_EXPIRY_SEC, min(3600, int(expiry_seconds or _EXECUTION_MIN_LIVE_EXPIRY_SEC)))

        descriptor = self._resolve_window_descriptor(payload)
        descriptor = self._activate_descriptor_for_execution(payload, descriptor)
        if descriptor is None:
            with self._lock:
                state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
                state["status"] = "blocked"
                state["message"] = "Demo execution blocked because the locked broker window is not visible."
                state["side"] = selected_side
                state["lane"] = "DEMO_RANDOM_TEST"
                state["expiry_seconds"] = selected_expiry
                state["recent_log"] = self._append_execution_log(
                    state,
                    status="blocked",
                    message=str(state["message"]),
                    side=selected_side,
                    lane="DEMO_RANDOM_TEST",
                )
                payload["broker_execution_state"] = state
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            return self.get_session(str(payload["session_id"]))

        window_image = self.capture_backend.capture_window(descriptor).convert("RGB")
        broker_surface = self._read_broker_surface(
            window_image,
            source="full_window_gui",
            manual_focus_region=manual_focus,
        )
        expiry_lock = _mapping_to_dict(broker_surface.get("expiry_lock", {}))
        expiry_lock["configured_seconds"] = selected_expiry
        expiry_lock["configured_text"] = PocketOptionBrokerExecutionBackend._format_expiry_text(selected_expiry)  # pyright: ignore[reportPrivateUsage]
        broker_surface["expiry_lock"] = expiry_lock

        state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
        state["enabled"] = True
        state["mode"] = "live"
        state["side"] = selected_side
        state["lane"] = "DEMO_RANDOM_TEST"
        state["actionable"] = True
        state["amount"] = _FIXED_BROKER_AMOUNT
        state["expiry_seconds"] = selected_expiry
        state["broker_surface"] = broker_surface
        state["last_attempt_at"] = _now_iso()
        state["last_attempt_epoch"] = _now_epoch()
        state["memory_gate"] = "demo_random_override"

        def finish(status: str, message: str) -> dict[str, Any]:
            state["status"] = status
            state["message"] = message
            state["recent_log"] = self._append_execution_log(
                state,
                status=status,
                message=message,
                side=selected_side,
                lane="DEMO_RANDOM_TEST",
            )
            with self._lock:
                payload["locked_window"] = dict(descriptor)
                payload["locked_title"] = str(descriptor.get("title", "") or "")
                payload["broker_surface"] = broker_surface
                payload["broker_execution_state"] = _normalize_broker_execution_state(state)
                latest_signal = _mapping_to_dict(payload.get("latest_signal", {}))
                latest_signal["broker_execution_state"] = payload["broker_execution_state"]
                latest_signal["demo_execution_side"] = selected_side
                latest_signal["demo_execution_expiry_seconds"] = selected_expiry
                payload["latest_signal"] = latest_signal
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
                self._write_session_event_log(
                    str(payload["session_id"]),
                    "demo_execution",
                    status=status,
                    side=selected_side,
                    expiry_seconds=int(selected_expiry),
                    message=message,
                    retry_block_until=str(state.get("retry_block_until", "") or ""),
                    last_result_status=str(_mapping_to_dict(state.get("last_result", {})).get("status", "") or ""),
                )
            return self.get_session(str(payload["session_id"]))

        if not bool(broker_surface.get("controls_ready", False)):
            return finish("blocked", "Demo execution blocked because broker BUY/SELL controls were not detected.")
        if not bool(_mapping_to_dict(broker_surface.get("amount_lock", {})).get("verified", False)):
            return finish("blocked", "Demo execution blocked because the fixed $5 amount field was not detected.")
        if not bool(_mapping_to_dict(broker_surface.get("expiry_lock", {})).get("field_ready", False)):
            return finish("blocked", "Demo execution blocked because the expiry/time field was not detected.")

        now_epoch = _now_epoch()
        active_trade = _mapping_to_dict(state.get("active_trade", {}))
        active_until = float(active_trade.get("expires_epoch", 0.0) or 0.0)
        if active_until > now_epoch:
            return finish("monitoring", f"Demo execution blocked because an existing {active_trade.get('side', 'trade')} trade is still active.")
        if active_trade:
            state["last_result"] = {
                "status": "expired_unverified",
                "message": "Previous demo trade window expired; broker outcome was not visually certified.",
                "resolved_at": _now_iso(),
                "trade": active_trade,
            }
            state["active_trade"] = {}
        controls = _normalize_execution_controls(payload.get("execution_controls", {}))
        retry_until = float(state.get("retry_block_until_epoch", 0.0) or 0.0)
        if retry_until > now_epoch and _same_execution_retry_target(
            _mapping_to_dict(state.get("last_result", {})),
            side=selected_side,
            lane="DEMO_RANDOM_TEST",
            expiry_seconds=selected_expiry,
        ):
            state["retry_block_until_epoch"] = retry_until
            state["retry_block_until"] = str(state.get("retry_block_until", "") or _epoch_to_utc_iso(retry_until))
            return finish("retry_wait", _execution_retry_backoff_message(state, now_epoch))
        state["retry_block_until_epoch"] = 0.0
        state["retry_block_until"] = ""
        throttle_allowed, throttle_message = self._execution_throttle_allows(state, controls, now_epoch=now_epoch)
        if not throttle_allowed:
            return finish("throttled", throttle_message)

        click_result = self.execution_backend.prepare_and_click(
            descriptor=descriptor,
            window_image=window_image,
            side=selected_side,
            amount=_FIXED_BROKER_AMOUNT,
            expiry_seconds=selected_expiry,
            broker_surface=broker_surface,
        )
        result_status = str(click_result.get("status", "") or "").lower()
        if result_status != "clicked":
            retry_backoff = (
                max(_EXECUTION_FAILED_ATTEMPT_BACKOFF_SEC, float(selected_expiry))
                if result_status == "click_sent_unverified"
                else _EXECUTION_FAILED_ATTEMPT_BACKOFF_SEC
            )
            _arm_execution_retry_backoff(
                state,
                now_epoch=now_epoch,
                side=selected_side,
                lane="DEMO_RANDOM_TEST",
                expiry_seconds=selected_expiry,
                result=click_result,
                backoff_sec=retry_backoff,
            )
            return finish(
                "click_sent_unverified" if result_status == "click_sent_unverified" else ("blocked" if result_status != "error" else "error"),
                str(click_result.get("message", "Demo broker click failed.") or "Demo broker click failed."),
            )

        trade_epoch = _now_epoch()
        cooldown_sec = float(controls.get("cooldown_sec", _EXECUTION_DEFAULT_COOLDOWN_SEC) or _EXECUTION_DEFAULT_COOLDOWN_SEC)
        state["last_trade_at"] = _now_iso()
        state["last_trade_epoch"] = trade_epoch
        state["cooldown_until_epoch"] = trade_epoch + cooldown_sec
        state["cooldown_until"] = _epoch_to_utc_iso(trade_epoch + cooldown_sec)
        state["last_result"] = dict(click_result)
        self._record_execution_throttle(state, controls, now_epoch=trade_epoch)
        state["active_trade"] = {
            "side": selected_side,
            "lane": "DEMO_RANDOM_TEST",
            "amount": _FIXED_BROKER_AMOUNT,
            "opened_at": _now_iso(),
            "opened_epoch": trade_epoch,
            "expires_at": _epoch_to_utc_iso(trade_epoch + selected_expiry),
            "expires_epoch": trade_epoch + selected_expiry,
            "expiry_seconds": selected_expiry,
            "click_result": dict(click_result),
        }
        return finish("clicked", str(click_result.get("message", f"Clicked demo {selected_side}.") or f"Clicked demo {selected_side}."))

    def run_memory_projection(self, session_id: str, *, mode: str) -> dict[str, Any]:
        normalized_mode = "future" if str(mode or "").strip().lower() == "future" else "predict"
        started_at = _now_iso()
        payload = self._require_session(session_id)
        manual_focus = _public_manual_focus_region(payload.get("manual_focus_region", {}))
        if not bool(manual_focus.get("enabled", False)):
            raise ValueError("Lock broker focus before running memory projection.")

        chart_path = self._artifact_path_if_exists(payload.get("last_chart_path", ""))
        if not chart_path:
            self._capture_and_analyze(str(payload["session_id"]), force=True)
            payload = self._require_session(session_id)
            chart_path = self._artifact_path_if_exists(payload.get("last_chart_path", ""))
        if not chart_path:
            raise ValueError("Capture a valid chart before running memory projection.")

        running_projection = _default_memory_projection_payload(
            mode=normalized_mode,
            message="Scanning PhoenixGuard memory bank and comparing live candle regression.",
            status="running",
        )
        running_projection.update(
            {
                "generated_at": started_at,
                "source_frame_index": int(payload.get("frame_index", 0) or 0),
                "source_chart_path": str(chart_path),
                "is_current": True,
                "memory_retrieval": {
                    "state": "running",
                    "message": "Scanning memory bank, scoring top analogs, and building forecast overlays.",
                    "bank_loaded": False,
                    "entries": 0,
                    "started_at": started_at,
                    "completed_at": "",
                },
            }
        )
        with self._lock:
            live_payload = self._require_session(session_id)
            live_payload[f"memory_projection_{normalized_mode}"] = running_projection
            live_payload["memory_projection_active_mode"] = normalized_mode
            live_payload["updated_at"] = started_at
            live_payload["last_error"] = ""
            self._save_session(live_payload)

        tracking_summary = _mapping_to_dict(payload.get("tracking_summary", {}))
        latest_signal = _mapping_to_dict(payload.get("latest_signal", {}))
        builder = getattr(self.tracking_adapter, "build_memory_projection", None)
        surface_image: Image.Image | None = None
        if not callable(builder):
            projection_payload = _default_memory_projection_payload(
                mode=normalized_mode,
                message="Tracker adapter does not support memory-backed projection actions.",
                status="error",
            )
        else:
            with Image.open(chart_path) as image:
                surface_image = image.convert("RGB")
            projection_payload = builder(
                surface_image,
                tracking_summary,
                latest_signal,
                mode=normalized_mode,
                session_payload=payload,
            )
        projection_payload = _normalize_memory_projection_payload(projection_payload, mode=normalized_mode)
        retrieval_state = _mapping_to_dict(projection_payload.get("memory_retrieval", {}))
        retrieval_state["started_at"] = str(retrieval_state.get("started_at", "") or started_at)
        retrieval_state["completed_at"] = str(retrieval_state.get("completed_at", "") or _now_iso())
        if str(retrieval_state.get("state", "") or "").strip().lower() in {"", "idle", "running"}:
            retrieval_state["state"] = "ready" if str(projection_payload.get("status", "")).lower() == "ready" else str(
                projection_payload.get("status", "degraded") or "degraded"
            )
        projection_payload["memory_retrieval"] = retrieval_state
        renderer = getattr(self.tracking_adapter, "render_memory_projection_artifacts", None)
        if callable(renderer) and surface_image is not None:
            chart_path_obj = Path(chart_path)
            artifact_dir = chart_path_obj.parent
            artifact_stem = chart_path_obj.stem
            rendered_artifacts = renderer(
                artifact_dir,
                artifact_stem,
                surface_image=surface_image,
                tracking_summary=tracking_summary,
                latest_signal=latest_signal,
                projection_payload=projection_payload,
            )
            if isinstance(rendered_artifacts, Mapping):
                projection_payload.update(dict(cast(Mapping[str, Any], rendered_artifacts)))
        projection_payload["generated_at"] = _now_iso()
        projection_payload["source_frame_index"] = int(payload.get("frame_index", 0) or 0)
        projection_payload["source_chart_path"] = str(chart_path)
        projection_payload["is_current"] = True

        with self._lock:
            payload = self._require_session(session_id)
            payload[f"memory_projection_{normalized_mode}"] = projection_payload
            payload["memory_projection_active_mode"] = normalized_mode
            payload["updated_at"] = _now_iso()
            payload["last_error"] = ""
            self._save_session(payload)
        return self.get_session(str(payload["session_id"]))

    def update_session_controls(
        self,
        session_id: str,
        *,
        capture_interval_sec: float | None = None,
        live_execution_enabled: bool | None = None,
        execution_mode: str | None = None,
        allow_countertrend_scalp: bool | None = None,
        scenario_generation_enabled: bool | None = None,
        auto_memory_projection: bool | None = None,
        require_memory_projection: bool | None = None,
        require_market_identity: bool | None = None,
        require_timeframe_identity: bool | None = None,
        adaptive_timer_enabled: bool | None = None,
        min_capture_interval_sec: float | None = None,
        max_capture_interval_sec: float | None = None,
        max_executions_per_window: int | None = None,
        execution_window_sec: float | None = None,
        min_market_confidence: float | None = None,
        min_timeframe_confidence: float | None = None,
        cooldown_sec: float | None = None,
    ) -> dict[str, Any]:
        payload = self._require_session(session_id)
        with self._lock:
            if capture_interval_sec is not None:
                payload["capture_interval_sec"] = max(_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC, float(capture_interval_sec))
            controls = _normalize_execution_controls(payload.get("execution_controls", {}))
            if live_execution_enabled is not None:
                controls["live_execution_enabled"] = bool(live_execution_enabled)
            if execution_mode is not None:
                controls["execution_mode"] = "live" if str(execution_mode or "").strip().lower() == "live" else "shadow"
            if allow_countertrend_scalp is not None:
                controls["allow_countertrend_scalp"] = bool(allow_countertrend_scalp)
            if scenario_generation_enabled is not None:
                controls["scenario_generation_enabled"] = bool(scenario_generation_enabled)
            if auto_memory_projection is not None:
                controls["auto_memory_projection"] = bool(auto_memory_projection)
            if require_memory_projection is not None:
                controls["require_memory_projection"] = bool(require_memory_projection)
            if require_market_identity is not None:
                controls["require_market_identity"] = bool(require_market_identity)
            if require_timeframe_identity is not None:
                controls["require_timeframe_identity"] = bool(require_timeframe_identity)
            if adaptive_timer_enabled is not None:
                controls["adaptive_timer_enabled"] = bool(adaptive_timer_enabled)
            if min_capture_interval_sec is not None:
                controls["min_capture_interval_sec"] = max(_EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC, float(min_capture_interval_sec))
            if max_capture_interval_sec is not None:
                controls["max_capture_interval_sec"] = max(_EXECUTION_DEFAULT_MAX_CAPTURE_INTERVAL_SEC, float(max_capture_interval_sec))
            if max_executions_per_window is not None:
                controls["max_executions_per_window"] = max(1, int(max_executions_per_window))
            if execution_window_sec is not None:
                controls["execution_window_sec"] = max(60.0, float(execution_window_sec))
            if min_market_confidence is not None:
                controls["min_market_confidence"] = _clip01(min_market_confidence)
            if min_timeframe_confidence is not None:
                controls["min_timeframe_confidence"] = _clip01(min_timeframe_confidence)
            if cooldown_sec is not None:
                controls["cooldown_sec"] = max(5.0, float(cooldown_sec))
            controls["fixed_amount"] = _FIXED_BROKER_AMOUNT
            payload["execution_controls"] = _normalize_execution_controls(controls)
            execution_state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
            execution_state["enabled"] = bool(payload["execution_controls"].get("live_execution_enabled", False))
            execution_state["mode"] = str(payload["execution_controls"].get("execution_mode", "shadow") or "shadow")
            if not execution_state["enabled"]:
                execution_state["status"] = "disabled"
                execution_state["message"] = "Live execution is disabled."
            else:
                execution_state["status"] = "shadow_ready" if execution_state["mode"] == "shadow" else "armed"
                execution_state["message"] = (
                    "Execution is enabled in shadow mode; PhoenixGuard will report trade decisions without clicking."
                    if execution_state["mode"] == "shadow"
                    else "Live execution is armed. PhoenixGuard will click only when all memory, identity, amount, cooldown, and timing gates pass."
                )
            payload["broker_execution_state"] = execution_state
            payload["updated_at"] = _now_iso()
            self._save_session(payload)
            self._write_session_event_log(
                str(payload["session_id"]),
                "controls_updated",
                capture_interval_sec=float(payload.get("capture_interval_sec", _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC) or _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC),
                live_execution_enabled=bool(payload["execution_controls"].get("live_execution_enabled", False)),
                execution_mode=str(payload["execution_controls"].get("execution_mode", "shadow") or "shadow"),
                min_capture_interval_sec=float(payload["execution_controls"].get("min_capture_interval_sec", 0.0) or 0.0),
                max_capture_interval_sec=float(payload["execution_controls"].get("max_capture_interval_sec", 0.0) or 0.0),
            )
            worker = self._workers.get(str(payload["session_id"]))
            if worker is not None:
                worker.capture_now_evt.set()
        return self.get_session(str(payload["session_id"]))

    def shutdown(self) -> None:
        self._emergency_hotkey_stop_evt.set()
        for session_id in list(self._workers):
            self._stop_worker(session_id)

    def _on_focus_selected(self, session_id: str, normalized_bbox: list[float], source: str) -> None:
        try:
            LOGGER.info("Focus selected for session %s: bbox=%s from %s", session_id, normalized_bbox, source)
            self.set_focus_region(session_id, normalized_bbox, source=source)
        except Exception:
            LOGGER.exception("Failed to apply broker focus selection for %s.", session_id)
            try:
                payload = self._load_session(session_id)
                if payload:
                    with self._lock:
                        error_message = "Failed to lock the selected broker focus region."
                        payload["focus_selector"] = _focus_selector_state(
                            supported=self.focus_selector_backend.is_supported(),
                            armed=False,
                            active=False,
                            status="error",
                            message=error_message,
                            last_error=error_message,
                            target_hwnd=int(payload.get("locked_window", {}).get("hwnd", 0) or 0),
                            target_title=str(payload.get("locked_title", "") or ""),
                        )
                        payload["last_error"] = error_message
                        payload["updated_at"] = _now_iso()
                        self._save_session(payload)
            except Exception:
                LOGGER.exception("Failed to update session error state for %s.", session_id)

    def _on_focus_state_change(self, session_id: str, state: dict[str, Any]) -> None:
        try:
            payload = self._load_session(session_id)
            if not payload:
                LOGGER.warning("Session %s not found when processing focus state change.", session_id)
                return
            with self._lock:
                payload["focus_selector"] = dict(state)
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            LOGGER.debug("Focus selector state updated for session %s: status=%s", session_id, state.get("status", "unknown"))
        except Exception:
            LOGGER.exception("Failed to update focus state for session %s.", session_id)

    def _ensure_worker(self, session_id: str, *, capture_now: bool = False) -> None:
        with self._lock:
            worker = self._workers.get(session_id)
            if worker is not None and worker.thread.is_alive():
                if capture_now:
                    worker.capture_now_evt.set()
                return
            stop_evt = threading.Event()
            capture_now_evt = threading.Event()
            if capture_now:
                capture_now_evt.set()
            thread = threading.Thread(
                target=self._worker_loop,
                args=(session_id, stop_evt, capture_now_evt),
                name=f"window-tracker-{session_id}",
                daemon=True,
            )
            self._workers[session_id] = _WorkerControl(
                thread=thread,
                stop_evt=stop_evt,
                capture_now_evt=capture_now_evt,
            )
            thread.start()

    def _stop_worker(self, session_id: str) -> None:
        with self._lock:
            worker = self._workers.get(session_id)
            if worker is None:
                self._next_capture_epoch.pop(session_id, None)
                return
            worker.stop_evt.set()
            worker.capture_now_evt.set()
        worker.thread.join(timeout=1.5)
        with self._lock:
            self._workers.pop(session_id, None)
            self._next_capture_epoch.pop(session_id, None)

    def _worker_loop(self, session_id: str, stop_evt: threading.Event, capture_now_evt: threading.Event) -> None:
        next_run = time.monotonic()
        last_signal_state_hash = ""
        pending_signal_state_hash = ""
        signal_state_change_due = 0.0
        signal_state_change_debounce_sec = 0.3
        try:
            while not stop_evt.is_set():
                payload = self._load_session(session_id)
                if not payload or not bool(payload.get("tracking_enabled", False)):
                    break
                now = time.monotonic()
                signal_state_hash = _tracker_signal_state_hash(
                    _mapping_to_dict(payload.get("latest_signal", {})),
                    _mapping_to_dict(payload.get("tracking_summary", {})),
                )
                if not last_signal_state_hash:
                    last_signal_state_hash = signal_state_hash
                elif signal_state_hash != last_signal_state_hash:
                    if signal_state_hash != pending_signal_state_hash:
                        pending_signal_state_hash = signal_state_hash
                        signal_state_change_due = now + signal_state_change_debounce_sec
                    elif signal_state_change_due > 0.0 and now >= signal_state_change_due:
                        LOGGER.info("Signal state changed for %s; firing immediate capture.", session_id)
                        last_signal_state_hash = signal_state_hash
                        pending_signal_state_hash = ""
                        signal_state_change_due = 0.0
                        capture_now_evt.set()
                else:
                    pending_signal_state_hash = ""
                    signal_state_change_due = 0.0
                if capture_now_evt.is_set():
                    capture_now_evt.clear()
                    next_run = time.monotonic()
                now = time.monotonic()
                if now < next_run:
                    remaining = next_run - now
                    with self._lock:
                        self._next_capture_epoch[session_id] = _now_epoch() + remaining
                    wait_timeout = min(0.25, max(0.05, remaining))
                    if signal_state_change_due > now:
                        wait_timeout = min(wait_timeout, max(0.05, signal_state_change_due - now))
                    capture_now_evt.wait(timeout=wait_timeout)
                    continue
                self._capture_and_analyze(session_id)
                payload = self._load_session(session_id)
                plan = self._adaptive_capture_interval_plan(payload or {})
                interval_sec = float(plan.get("interval_sec", _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC) or _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC)
                next_run = max(next_run + interval_sec, time.monotonic() + interval_sec)
                with self._lock:
                    self._next_capture_epoch[session_id] = _now_epoch() + interval_sec
        except Exception:
            LOGGER.exception("Tracker worker crashed for session %s.", session_id)
            payload = self._load_session(session_id)
            if payload:
                with self._lock:
                    payload["tracking_enabled"] = False
                    payload["status"] = "error"
                    payload["last_error"] = "Tracker worker crashed. Review logs and restart the tracker."
                    payload["latest_signal"] = _default_signal(
                        message="Tracker worker crashed. Review logs and restart the tracker.",
                        status="error",
                    )
                    payload["updated_at"] = _now_iso()
                    self._save_session(payload)
                    self._write_session_event_log(
                        session_id,
                        "worker_crashed",
                        status="error",
                        message=str(payload["last_error"]),
                    )
        finally:
            with self._lock:
                self._workers.pop(session_id, None)
                self._next_capture_epoch.pop(session_id, None)

    def _adaptive_capture_interval_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        base_interval = max(
            _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC,
            float(payload.get("capture_interval_sec", _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC) or _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC),
        )
        controls = _normalize_execution_controls(payload.get("execution_controls", {}))
        if not bool(controls.get("adaptive_timer_enabled", True)):
            return {"interval_sec": base_interval, "reason": "fixed_timer"}

        min_interval = max(_EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC, float(controls.get("min_capture_interval_sec", _EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC) or _EXECUTION_DEFAULT_MIN_CAPTURE_INTERVAL_SEC))
        max_interval = max(min_interval, float(controls.get("max_capture_interval_sec", _EXECUTION_DEFAULT_MAX_CAPTURE_INTERVAL_SEC) or _EXECUTION_DEFAULT_MAX_CAPTURE_INTERVAL_SEC))

        manual_focus = _public_manual_focus_region(payload.get("manual_focus_region", {}))
        if not bool(manual_focus.get("enabled", False)):
            return {"interval_sec": min(max(base_interval, min_interval), max_interval), "reason": "awaiting_focus"}

        latest_signal = _mapping_to_dict(payload.get("latest_signal", {}))
        tracking_summary = _mapping_to_dict(payload.get("tracking_summary", {}))
        execution_state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
        decision_kernel = _mapping_to_dict(
            tracking_summary.get("decision_kernel", latest_signal.get("decision_kernel", {}))
        )
        entry_state = str(latest_signal.get("entry_state", tracking_summary.get("entry_state", "")) or "").upper()
        kernel_state = str(decision_kernel.get("state", "") or "").upper()
        execution_side = _upper_action(latest_signal.get("execution_action", latest_signal.get("action", "HOLD")))
        actionable = bool(latest_signal.get("actionable", False)) and execution_side in {"BUY", "SELL"}
        p_trigger_next_1 = _clip01(decision_kernel.get("p_trigger_next_1", 0.0))
        p_trigger_next_3 = _clip01(decision_kernel.get("p_trigger_next_3", 0.0))
        active_trade = _mapping_to_dict(execution_state.get("active_trade", {}))
        active_until = float(active_trade.get("expires_epoch", 0.0) or 0.0)
        status = str(execution_state.get("status", "") or "").lower()

        if active_until > _now_epoch() or status in {"clicked", "monitoring"}:
            interval = min(base_interval, max(min_interval, 3.0))
            return {"interval_sec": min(max(interval, min_interval), max_interval), "reason": "monitor_active_trade"}
        if actionable or kernel_state in {"TRIGGERED", "ACTIVE"} or "READY" in entry_state:
            return {"interval_sec": min_interval, "reason": "entry_ready"}
        if (
            entry_state in {"WAIT_FOR_SNIPER", "WAIT_FOR_TRIGGER", "SNIPER_WATCH", "WATCH"}
            or kernel_state == "ARMED"
            or p_trigger_next_1 >= 0.35
            or p_trigger_next_3 >= 0.55
        ):
            return {"interval_sec": min_interval, "reason": "sniper_watch"}

        return {"interval_sec": min(max(base_interval, min_interval), max_interval), "reason": "base_timer"}

    def _normalize_execution_throttle(
        self,
        state: Mapping[str, Any],
        controls: Mapping[str, Any],
        *,
        now_epoch: float | None = None,
    ) -> dict[str, Any]:
        now = _now_epoch() if now_epoch is None else float(now_epoch)
        raw = _mapping_to_dict(state.get("throttle", {}))
        max_executions = max(1, int(controls.get("max_executions_per_window", 5) or 5))
        window_seconds = max(60.0, float(controls.get("execution_window_sec", 300) or 300))
        window_started = max(0.0, float(raw.get("window_started_epoch", 0.0) or 0.0))
        executions = max(0, int(raw.get("executions_in_window", 0) or 0))
        if window_started <= 0.0 or now - window_started >= window_seconds:
            window_started = now
            executions = 0
        blocked_until = window_started + window_seconds if executions >= max_executions else 0.0
        return {
            "window_started_epoch": window_started,
            "window_started_at": _epoch_to_utc_iso(window_started),
            "executions_in_window": executions,
            "max_executions": max_executions,
            "window_seconds": window_seconds,
            "blocked_until_epoch": blocked_until,
            "blocked_until": _epoch_to_utc_iso(blocked_until) if blocked_until > now else "",
            "message": (
                f"Execution throttle used {executions}/{max_executions} clicks in the current {int(window_seconds)}s window."
            ),
        }

    def _execution_throttle_allows(
        self,
        state: dict[str, Any],
        controls: Mapping[str, Any],
        *,
        now_epoch: float | None = None,
    ) -> tuple[bool, str]:
        now = _now_epoch() if now_epoch is None else float(now_epoch)
        throttle = self._normalize_execution_throttle(state, controls, now_epoch=now)
        state["throttle"] = throttle
        blocked_until = float(throttle.get("blocked_until_epoch", 0.0) or 0.0)
        if blocked_until > now:
            remaining = max(0.0, blocked_until - now)
            return False, f"Execution throttle is active for {remaining:.1f}s after {throttle['executions_in_window']}/{throttle['max_executions']} clicks."
        return True, str(throttle.get("message", "") or "")

    def _record_execution_throttle(
        self,
        state: dict[str, Any],
        controls: Mapping[str, Any],
        *,
        now_epoch: float | None = None,
    ) -> None:
        now = _now_epoch() if now_epoch is None else float(now_epoch)
        throttle = self._normalize_execution_throttle(state, controls, now_epoch=now)
        throttle["executions_in_window"] = int(throttle.get("executions_in_window", 0) or 0) + 1
        if int(throttle["executions_in_window"]) >= int(throttle["max_executions"]):
            blocked_until = float(throttle["window_started_epoch"]) + float(throttle["window_seconds"])
            throttle["blocked_until_epoch"] = blocked_until
            throttle["blocked_until"] = _epoch_to_utc_iso(blocked_until)
        throttle["message"] = (
            f"Execution throttle used {throttle['executions_in_window']}/{throttle['max_executions']} clicks in the current {int(float(throttle['window_seconds']))}s window."
        )
        state["throttle"] = throttle

    def _append_execution_log(
        self,
        state: Mapping[str, Any],
        *,
        status: str,
        message: str,
        side: str = "HOLD",
        lane: str = "NONE",
    ) -> list[dict[str, Any]]:
        rows = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], state.get("recent_log", []))
            if isinstance(item, Mapping)
        ]
        rows.insert(
            0,
            {
                "timestamp": _now_iso(),
                "status": str(status or ""),
                "message": str(message or ""),
                "side": _upper_action(side),
                "lane": str(lane or "NONE").upper(),
            },
        )
        return rows[:20]

    def _read_broker_surface(
        self,
        window_image: Image.Image,
        *,
        source: str = "full_window_gui",
        manual_focus_region: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            surface = self.execution_backend.read_surface(window_image)
        except Exception as exc:
            LOGGER.exception("Broker surface read failed.")
            surface = _default_broker_surface_payload(message=f"Broker surface read failed: {exc}")
        focus = _public_manual_focus_region(manual_focus_region or {})
        source_name = str(source or "full_window_gui").strip() or "full_window_gui"
        surface["capture_plane"] = {
            "source": source_name,
            "width": int(window_image.width),
            "height": int(window_image.height),
            "uses_manual_focus_crop": source_name != "full_window_gui",
            "manual_focus_bbox": list(cast(Sequence[Any], focus.get("normalized_bbox", [])))
            if bool(focus.get("enabled", False))
            else [],
            "message": (
                "Execution controls are read from the full Pocket Option window capture; the chart focus crop is only for candle study."
                if source_name == "full_window_gui"
                else "Execution controls are being read from a cropped surface."
            ),
        }
        return surface

    def _apply_broker_identity(
        self,
        tracking_summary: dict[str, Any],
        latest_signal: dict[str, Any],
        broker_surface: Mapping[str, Any],
        session_payload: Mapping[str, Any],
    ) -> None:
        broker = _mapping_to_dict(broker_surface)
        broker_market = _normalize_fx_market_candidate(broker.get("detected_market", ""))
        broker_timeframe = str(broker.get("detected_timeframe", "") or "").strip().upper()
        broker_market_conf = _clip01(broker.get("market_confidence", 0.0)) if broker_market else 0.0
        broker_timeframe_conf = _clip01(broker.get("timeframe_confidence", 0.0))

        current_market = _normalize_fx_market_candidate(latest_signal.get("market", tracking_summary.get("detected_market", "")))
        current_market_conf = _clip01(latest_signal.get("market_confidence", tracking_summary.get("market_confidence", 0.0)))
        if not current_market:
            current_market_conf = 0.0
        if broker_market and broker_market_conf >= max(0.01, current_market_conf):
            tracking_summary["detected_market"] = broker_market
            tracking_summary["market_source"] = str(broker.get("market_source", "broker_header") or "broker_header")
            tracking_summary["market_confidence"] = broker_market_conf
            latest_signal["market"] = broker_market
            latest_signal["market_source"] = tracking_summary["market_source"]
            latest_signal["market_confidence"] = broker_market_conf
        elif current_market:
            latest_signal["market"] = current_market
            latest_signal["market_confidence"] = current_market_conf
        elif _normalize_fx_market_candidate(session_payload.get("market", "")):
            latest_signal["market"] = _normalize_fx_market_candidate(session_payload.get("market", ""))
        else:
            latest_signal["market"] = ""
            latest_signal["market_confidence"] = 0.0

        current_timeframe = str(latest_signal.get("focus_timeframe", tracking_summary.get("detected_timeframe", "")) or "").strip().upper()
        current_timeframe_conf = _clip01(tracking_summary.get("timeframe_confidence", 0.0))
        if broker_timeframe and broker_timeframe_conf >= max(0.01, current_timeframe_conf):
            tracking_summary["detected_timeframe"] = broker_timeframe
            tracking_summary["timeframe_source"] = str(broker.get("timeframe_source", "broker_header") or "broker_header")
            tracking_summary["timeframe_confidence"] = broker_timeframe_conf
            latest_signal["focus_timeframe"] = broker_timeframe
            latest_signal["focus_timeframe_source"] = tracking_summary["timeframe_source"]
        elif current_timeframe:
            latest_signal["focus_timeframe"] = current_timeframe

        tracking_summary["broker_identity"] = {
            "detected_market": str(tracking_summary.get("detected_market", "") or ""),
            "market_confidence": _clip01(tracking_summary.get("market_confidence", 0.0)),
            "market_source": str(tracking_summary.get("market_source", "unconfirmed") or "unconfirmed"),
            "detected_timeframe": str(tracking_summary.get("detected_timeframe", "") or ""),
            "timeframe_confidence": _clip01(tracking_summary.get("timeframe_confidence", 0.0)),
            "timeframe_source": str(tracking_summary.get("timeframe_source", "unconfirmed") or "unconfirmed"),
            "broker_detected_market": broker_market,
            "broker_market_confidence": broker_market_conf,
            "broker_detected_timeframe": broker_timeframe,
            "broker_timeframe_confidence": broker_timeframe_conf,
            "identity_ready": bool(
                str(tracking_summary.get("detected_market", "") or "").strip()
                and str(tracking_summary.get("detected_timeframe", "") or "").strip()
                and _clip01(tracking_summary.get("market_confidence", 0.0)) >= 0.42
                and _clip01(tracking_summary.get("timeframe_confidence", 0.0)) >= 0.42
            ),
        }

    def _select_execution_lane(
        self,
        latest_signal: Mapping[str, Any],
        tracking_summary: Mapping[str, Any],
        controls: Mapping[str, Any],
    ) -> dict[str, Any]:
        execution_side = _upper_action(latest_signal.get("execution_action", "HOLD"))
        is_actionable = bool(latest_signal.get("actionable", False))
        
        # PRIMARY LANE: TREND FOLLOW
        if is_actionable and execution_side in {"BUY", "SELL"}:
            risk_gate = self._execution_risk_gate(execution_side, latest_signal, tracking_summary)
            if not bool(risk_gate.get("accepted", True)):
                LOGGER.info(f"Trend-follow entry blocked by risk gate: {risk_gate.get('reason', '')}")
                return {
                    "side": "HOLD",
                    "lane": "RISK_GATE",
                    "actionable": False,
                    "reason": str(risk_gate.get("reason", "Execution risk gate blocked the entry.") or ""),
                }
            LOGGER.info(f"Trend-follow {execution_side} ACTIVATED - proceeding to broker execution")
            return {
                "side": execution_side,
                "lane": "TREND_FOLLOW",
                "actionable": True,
                "reason": str(latest_signal.get("summary", "") or "Primary execution gate is ready."),
            }
        
        # SECONDARY LANE: COUNTERTREND SCALP
        countertrend_lane = _mapping_to_dict(latest_signal.get("countertrend_lane", {}))
        if not countertrend_lane:
            countertrend_lane = _mapping_to_dict(tracking_summary.get("countertrend_lane", {}))
        counter_side = _upper_action(countertrend_lane.get("side", "HOLD"))
        counter_actionable = bool(countertrend_lane.get("actionable", False))
        
        if (
            bool(controls.get("allow_countertrend_scalp", False))
            and counter_actionable
            and counter_side in {"BUY", "SELL"}
        ):
            risk_gate = self._execution_risk_gate(counter_side, latest_signal, tracking_summary)
            if not bool(risk_gate.get("accepted", True)):
                LOGGER.info(f"Countertrend scalp blocked by risk gate: {risk_gate.get('reason', '')}")
                return {
                    "side": "HOLD",
                    "lane": "RISK_GATE",
                    "actionable": False,
                    "reason": str(risk_gate.get("reason", "Execution risk gate blocked the scalp.") or ""),
                }
            LOGGER.info(f"Countertrend scalp {counter_side} ACTIVATED - proceeding to broker execution")
            return {
                "side": counter_side,
                "lane": "COUNTERTREND_SCALP",
                "actionable": True,
                "reason": str(countertrend_lane.get("instruction", "") or "Countertrend scalp lane is ready."),
            }
        
        # NO EXECUTABLE LANE
        return {
            "side": "HOLD",
            "lane": "NONE",
            "actionable": False,
            "reason": "No executable trend-follow or countertrend scalp lane is ready.",
        }

    def _execution_risk_gate(
        self,
        side: str,
        latest_signal: Mapping[str, Any],
        tracking_summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        side_action = _upper_action(side)
        kernel = _mapping_to_dict(tracking_summary.get("decision_kernel", latest_signal.get("decision_kernel", {})))
        candle_stats = _mapping_to_dict(tracking_summary.get("candle_statistics", {}))
        if not kernel and not candle_stats:
            return {"accepted": True, "reason": "No risk-kernel metrics were available."}

        conflict_score = _clip01(kernel.get("conflict_score", tracking_summary.get("conflict_score", 0.0)))
        opposing_ratio = _clip01(candle_stats.get("opposing_ratio", tracking_summary.get("opposing_ratio", 0.0)))
        failure_risk = _clip01(
            _mapping_to_dict(tracking_summary.get("box_context", {})).get(
                "failure_risk",
                tracking_summary.get("failure_risk", 0.0),
            )
        )
        p_target_before_invalidation = _clip01(kernel.get("p_target_before_invalidation", 0.0))
        hazard_invalidation = _clip01(kernel.get("hazard_invalidation", 0.0))
        hazard_trigger = _clip01(kernel.get("hazard_trigger", 0.0))
        p_expire_before_trigger = _clip01(kernel.get("p_expire_before_trigger", 0.0))
        next_event = str(kernel.get("next_most_likely_event", "") or "").strip().lower()
        next_bias = _upper_action(kernel.get("next_candle_bias", latest_signal.get("action", "HOLD")))
        p_next_buy = _clip01(kernel.get("p_next_buy", 0.0))
        p_next_sell = _clip01(kernel.get("p_next_sell", 0.0))
        map_timing = _mapping_to_dict(tracking_summary.get("map_timing", latest_signal.get("map_timing", {})))
        if bool(map_timing.get("target_reached", latest_signal.get("target_reached", False))) or str(kernel.get("state", "") or "").upper() == "COMPLETE":
            return {
                "accepted": False,
                "reason": "Target zone is already reached; the move is complete and live execution must monitor/reset.",
            }
        relax_down = 0.90
        relax_up = 1.10
        conflict_limit = 0.72 * relax_down
        opposing_ratio_limit = 0.48 * relax_down
        opposing_target_limit = 0.58 * relax_down
        failure_risk_limit = 0.62 * relax_down
        target_floor = 0.46 * relax_down
        hazard_multiplier = 1.35 * relax_up
        expire_before_trigger_limit = 0.70 * relax_down
        next_bias_margin = 0.16 * relax_up

        if conflict_score >= conflict_limit:
            return {"accepted": False, "reason": f"Conflict score {conflict_score:.2f} is too high for live execution."}
        if opposing_ratio >= opposing_ratio_limit and p_target_before_invalidation < opposing_target_limit:
            return {
                "accepted": False,
                "reason": f"Opposing candle pressure {opposing_ratio:.2f} is too close to the entry side.",
            }
        if failure_risk >= failure_risk_limit:
            return {"accepted": False, "reason": f"Failure risk {failure_risk:.2f} is above the execution limit."}
        if p_target_before_invalidation > 0.0 and p_target_before_invalidation < target_floor:
            return {
                "accepted": False,
                "reason": f"Target-before-invalidation probability {p_target_before_invalidation:.2f} is too weak.",
            }
        if hazard_invalidation > 0.0 and hazard_trigger > 0.0 and hazard_invalidation >= hazard_trigger * hazard_multiplier:
            return {
                "accepted": False,
                "reason": "Invalidation hazard is materially stronger than trigger hazard.",
            }
        if p_expire_before_trigger >= expire_before_trigger_limit:
            return {"accepted": False, "reason": "The current setup is likely to expire before a clean trigger."}
        if next_event in {"invalidation", "stale"} and str(kernel.get("state", "") or "").upper() not in {"TRIGGERED", "ACTIVE"}:
            return {"accepted": False, "reason": f"Decision kernel expects {next_event} before a clean entry."}
        if side_action == "BUY" and next_bias == "SELL" and p_next_sell >= p_next_buy + next_bias_margin:
            return {"accepted": False, "reason": "Next-candle bias opposes the BUY entry."}
        if side_action == "SELL" and next_bias == "BUY" and p_next_buy >= p_next_sell + next_bias_margin:
            return {"accepted": False, "reason": "Next-candle bias opposes the SELL entry."}
        return {"accepted": True, "reason": "Decision-kernel risk gate accepted the entry."}

    def _execution_expiry_seconds(
        self,
        latest_signal: Mapping[str, Any],
        tracking_summary: Mapping[str, Any],
        *,
        lane: str,
    ) -> int:
        timeframe = str(latest_signal.get("focus_timeframe", tracking_summary.get("detected_timeframe", "M5")) or "M5").upper()
        timeframe_sec = _timeframe_seconds(timeframe, default=300)
        decision_kernel = _mapping_to_dict(tracking_summary.get("decision_kernel", latest_signal.get("decision_kernel", {})))
        hold_candles = max(1, int(decision_kernel.get("hold_for_candles", 1) or 1))
        target_eta = max(1, int(decision_kernel.get("eta_target_after_trigger_candles", hold_candles) or hold_candles))
        invalidation_eta = max(1, int(decision_kernel.get("eta_invalidation_candles", hold_candles + 1) or (hold_candles + 1)))
        target_before_invalidation = _clip01(decision_kernel.get("p_target_before_invalidation", 0.0))
        if str(lane or "").upper() == "COUNTERTREND_SCALP":
            hold_candles = 3
        else:
            if target_before_invalidation >= 0.58 and target_eta < invalidation_eta:
                hold_candles = max(hold_candles, target_eta)
            hold_candles = max(1, min(18, max(hold_candles, min(target_eta, max(1, invalidation_eta + 1)))))
        return int(max(_EXECUTION_MIN_LIVE_EXPIRY_SEC, timeframe_sec * hold_candles))

    def _memory_projection_for_execution(
        self,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        *,
        side: str,
        session_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        builder = getattr(self.tracking_adapter, "build_memory_projection", None)
        if not callable(builder):
            return _default_memory_projection_payload(
                mode="predict",
                status="degraded",
                message="Execution memory gate cannot run because the tracking adapter has no memory projection builder.",
            )
        side_action = _upper_action(side)
        execution_tracking = _mapping_to_dict(tracking_summary)
        execution_signal = _mapping_to_dict(latest_signal)
        projection = _mapping_to_dict(execution_tracking.get("projection", {}))
        projection["direction"] = side_action
        for raw_zone in cast(Sequence[Any], projection.get("zones", [])):
            if isinstance(raw_zone, dict):
                raw_zone["direction"] = side_action
        execution_tracking["projection"] = projection
        execution_signal["action"] = side_action
        execution_signal["candidate_action"] = side_action
        execution_signal["execution_action"] = side_action
        execution_signal["actionable"] = True
        try:
            return _normalize_memory_projection_payload(
                builder(
                    surface_image,
                    execution_tracking,
                    execution_signal,
                    mode="predict",
                    session_payload=session_payload,
                ),
                mode="predict",
            )
        except Exception as exc:
            LOGGER.exception("Execution memory projection failed.")
            return _default_memory_projection_payload(
                mode="predict",
                status="degraded",
                message=f"Execution memory gate failed: {exc}",
            )

    def _evaluate_broker_execution(
        self,
        *,
        payload: Mapping[str, Any],
        descriptor: Mapping[str, Any],
        window_image: Image.Image,
        surface_image: Image.Image,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        controls = _normalize_execution_controls(payload.get("execution_controls", {}))
        previous_state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
        broker_surface = self._read_broker_surface(
            window_image,
            source="full_window_gui",
            manual_focus_region=_public_manual_focus_region(payload.get("manual_focus_region", {})),
        )
        if isinstance(tracking_summary, dict) and isinstance(latest_signal, dict):
            self._apply_broker_identity(tracking_summary, latest_signal, broker_surface, payload)
        state = _normalize_broker_execution_state(previous_state)
        state["enabled"] = bool(controls.get("live_execution_enabled", False))
        state["mode"] = str(controls.get("execution_mode", "shadow") or "shadow")
        state["broker_surface"] = broker_surface
        state["amount"] = _FIXED_BROKER_AMOUNT
        state["last_attempt_at"] = _now_iso()

        selected = self._select_execution_lane(latest_signal, tracking_summary, controls)
        side = _upper_action(selected.get("side", "HOLD"))
        lane = str(selected.get("lane", "NONE") or "NONE").upper()
        expiry_seconds = self._execution_expiry_seconds(latest_signal, tracking_summary, lane=lane)
        state["side"] = side
        state["lane"] = lane
        state["expiry_seconds"] = int(expiry_seconds)
        state["actionable"] = bool(selected.get("actionable", False))
        broker_expiry_lock = _mapping_to_dict(broker_surface.get("expiry_lock", {}))
        broker_expiry_lock["configured_seconds"] = int(expiry_seconds)
        broker_expiry_lock["configured_text"] = PocketOptionBrokerExecutionBackend._format_expiry_text(expiry_seconds)  # pyright: ignore[reportPrivateUsage]
        broker_surface["expiry_lock"] = broker_expiry_lock

        def block(status: str, message: str, memory_projection: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
            state["status"] = status
            state["message"] = message
            state["memory_projection"] = memory_projection or state.get("memory_projection", {})
            state["recent_log"] = self._append_execution_log(state, status=status, message=message, side=side, lane=lane)
            return broker_surface, _normalize_broker_execution_state(state), memory_projection

        if not bool(controls.get("live_execution_enabled", False)):
            return block("disabled", "Live execution is disabled. Tracker will only report.")
        if not bool(broker_surface.get("controls_ready", False)):
            return block("blocked", "Broker BUY/SELL controls were not confidently detected.")
        if not bool(_mapping_to_dict(broker_surface.get("amount_lock", {})).get("verified", False)):
            return block("blocked", "Amount field was not detected; fixed $5 execution is blocked.")
        expiry_lock = _mapping_to_dict(broker_surface.get("expiry_lock", {}))
        if not bool(expiry_lock.get("field_ready", False)):
            return block("blocked", "Expiry/time field was not detected; timed execution is blocked.")
        now_epoch = _now_epoch()
        active_trade = _mapping_to_dict(previous_state.get("active_trade", {}))
        active_until = float(active_trade.get("expires_epoch", 0.0) or 0.0)
        if active_until > now_epoch:
            state["active_trade"] = active_trade
            state["last_trade_at"] = str(previous_state.get("last_trade_at", "") or state.get("last_trade_at", ""))
            state["last_trade_epoch"] = float(previous_state.get("last_trade_epoch", 0.0) or 0.0)
            state["cooldown_until_epoch"] = float(previous_state.get("cooldown_until_epoch", 0.0) or 0.0)
            state["cooldown_until"] = str(previous_state.get("cooldown_until", "") or "")
            state["last_result"] = _mapping_to_dict(previous_state.get("last_result", state.get("last_result", {})))
            return block("monitoring", f"Existing {active_trade.get('side', 'trade')} trade is still being monitored.")
        if active_trade:
            state["last_result"] = {
                "status": "expired_unverified",
                "message": "Previous trade window expired; broker outcome was not visually certified.",
                "resolved_at": _now_iso(),
                "trade": active_trade,
            }
            state["active_trade"] = {}
        if not bool(selected.get("actionable", False)) or side not in {"BUY", "SELL"}:
            return block("watching", str(selected.get("reason", "") or "No executable lane is ready."))

        if bool(controls.get("require_market_identity", True)):
            market_conf = _clip01(latest_signal.get("market_confidence", tracking_summary.get("market_confidence", 0.0)))
            if market_conf < _clip01(controls.get("min_market_confidence", 0.42)):
                return block("blocked", f"Market identity confidence {market_conf:.2f} is below live execution gate.")
        if bool(controls.get("require_timeframe_identity", False)):
            timeframe_conf = _clip01(tracking_summary.get("timeframe_confidence", 0.0))
            if timeframe_conf < _clip01(controls.get("min_timeframe_confidence", 0.42)):
                return block("blocked", f"Timeframe identity confidence {timeframe_conf:.2f} is below live execution gate.")

        cooldown_until = float(previous_state.get("cooldown_until_epoch", 0.0) or 0.0)
        if cooldown_until > now_epoch:
            state["cooldown_until_epoch"] = cooldown_until
            state["cooldown_until"] = str(previous_state.get("cooldown_until", "") or "")
            return block("cooldown", f"Execution cooldown is active for {cooldown_until - now_epoch:.1f}s.")
        retry_until = float(previous_state.get("retry_block_until_epoch", 0.0) or 0.0)
        last_result = _mapping_to_dict(previous_state.get("last_result", {}))
        if retry_until > now_epoch and _same_execution_retry_target(
            last_result,
            side=side,
            lane=lane,
            expiry_seconds=expiry_seconds,
        ):
            state["retry_block_until_epoch"] = retry_until
            state["retry_block_until"] = str(previous_state.get("retry_block_until", "") or _epoch_to_utc_iso(retry_until))
            state["last_result"] = last_result
            return block("retry_wait", _execution_retry_backoff_message(state, now_epoch), None)
        state["retry_block_until_epoch"] = 0.0
        state["retry_block_until"] = ""
        if str(controls.get("execution_mode", "shadow") or "shadow") == "live":
            throttle_allowed, throttle_message = self._execution_throttle_allows(state, controls, now_epoch=now_epoch)
            if not throttle_allowed:
                return block("throttled", throttle_message)

        memory_projection: dict[str, Any] | None = None
        if bool(controls.get("require_memory_projection", True)):
            if not bool(controls.get("auto_memory_projection", True)):
                return block("blocked", "Memory projection is required but automatic execution memory projection is disabled.")
            memory_projection = self._memory_projection_for_execution(
                surface_image,
                tracking_summary,
                latest_signal,
                side=side,
                session_payload=payload,
            )
            state["memory_projection"] = {
                "status": str(memory_projection.get("status", "") or ""),
                "dominant_side": _upper_action(memory_projection.get("dominant_side", "HOLD")),
                "memory_similarity": _clip01(memory_projection.get("memory_similarity", 0.0)),
                "memory_precision_score": _clip01(memory_projection.get("memory_precision_score", 0.0)),
                "memory_edge": float(memory_projection.get("memory_edge", 0.0) or 0.0),
                "summary": str(memory_projection.get("summary", "") or ""),
            }
            precision = _mapping_to_dict(memory_projection.get("memory_precision", {}))
            if str(memory_projection.get("status", "") or "").lower() != "ready" or not bool(precision.get("accepted", False)):
                return block(
                    "blocked",
                    str(precision.get("reason", "") or memory_projection.get("summary", "Memory gate rejected execution.")),
                    memory_projection,
                )
            if _upper_action(memory_projection.get("dominant_side", side)) != side:
                return block("blocked", "Memory gate dominant side does not match the selected execution side.", memory_projection)
            state["memory_gate"] = "accepted"
        else:
            state["memory_gate"] = "not_required"

        if str(controls.get("execution_mode", "shadow") or "shadow") != "live":
            message = f"Shadow execution: {side} {lane.lower().replace('_', ' ')} would click with fixed $5 for {expiry_seconds}s."
            return block("shadow_ready", message, memory_projection)

        state["last_attempt_epoch"] = now_epoch
        state["last_attempt_at"] = _now_iso()
        click_result = self.execution_backend.prepare_and_click(
            descriptor=descriptor,
            window_image=window_image,
            side=side,
            amount=_FIXED_BROKER_AMOUNT,
            expiry_seconds=expiry_seconds,
            broker_surface=broker_surface,
        )
        result_status = str(click_result.get("status", "") or "").lower()
        if result_status != "clicked":
            retry_backoff = (
                max(_EXECUTION_FAILED_ATTEMPT_BACKOFF_SEC, float(expiry_seconds))
                if result_status == "click_sent_unverified"
                else _EXECUTION_FAILED_ATTEMPT_BACKOFF_SEC
            )
            _arm_execution_retry_backoff(
                state,
                now_epoch=now_epoch,
                side=side,
                lane=lane,
                expiry_seconds=expiry_seconds,
                result=click_result,
                backoff_sec=retry_backoff,
            )
            return block(
                "click_sent_unverified" if result_status == "click_sent_unverified" else ("blocked" if result_status != "error" else "error"),
                str(click_result.get("message", "Broker click failed.") or "Broker click failed."),
                memory_projection,
            )

        trade_epoch = _now_epoch()
        cooldown_sec = float(controls.get("cooldown_sec", _EXECUTION_DEFAULT_COOLDOWN_SEC) or _EXECUTION_DEFAULT_COOLDOWN_SEC)
        state["status"] = "clicked"
        state["message"] = str(click_result.get("message", f"Clicked {side}.") or f"Clicked {side}.")
        state["last_trade_at"] = _now_iso()
        state["last_trade_epoch"] = trade_epoch
        state["cooldown_until_epoch"] = trade_epoch + cooldown_sec
        state["cooldown_until"] = _epoch_to_utc_iso(trade_epoch + cooldown_sec)
        self._record_execution_throttle(state, controls, now_epoch=trade_epoch)
        state["active_trade"] = {
            "side": side,
            "lane": lane,
            "amount": _FIXED_BROKER_AMOUNT,
            "opened_at": _now_iso(),
            "opened_epoch": trade_epoch,
            "expires_at": _epoch_to_utc_iso(trade_epoch + expiry_seconds),
            "expires_epoch": trade_epoch + expiry_seconds,
            "expiry_seconds": int(expiry_seconds),
            "click_result": dict(click_result),
        }
        state["recent_log"] = self._append_execution_log(state, status="clicked", message=state["message"], side=side, lane=lane)
        return broker_surface, _normalize_broker_execution_state(state), memory_projection

    def _scenario_chart_state_for_tracker(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> dict[str, Any]:
        tracked_candles = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], tracking_summary.get("tracked_candles", []))
            if isinstance(item, Mapping)
        ]
        if not tracked_candles:
            return {}

        action = _upper_action(latest_signal.get("action", "HOLD"))
        confidence = _clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", 0.5)))

        def _read_ohlc(candle: Mapping[str, Any], key_o: str, key_h: str, key_l: str, key_c: str, fallback: float) -> tuple[float, float, float, float]:
            o = _float_or(candle.get(key_o, candle.get("open", fallback)), fallback)
            h = _float_or(candle.get(key_h, candle.get("high", fallback)), fallback)
            l = _float_or(candle.get(key_l, candle.get("low", fallback)), fallback)
            c = _float_or(candle.get(key_c, candle.get("close", fallback)), fallback)
            return o, h, l, c

        entry = tracked_candles[-1]
        fallback = _float_or(tracking_summary.get("latest_price_proxy", 1.0), 1.0)
        o, h, l, c = _read_ohlc(entry, "o", "h", "l", "c", fallback)

        recent_candles: list[dict[str, Any]] = []
        for row in tracked_candles[-20:]:
            ro, rh, rl, rc = _read_ohlc(row, "o", "h", "l", "c", c)
            recent_candles.append(
                {
                    "o": ro,
                    "h": max(ro, rh, rc),
                    "l": min(ro, rl, rc),
                    "c": rc,
                    "v": 1.0,
                    "dir": _upper_action(row.get("direction", action)),
                    "conf": _clip01(row.get("confidence", confidence)),
                }
            )

        return {
            "entry_candle": {
                "o": o,
                "h": max(o, h, c),
                "l": min(o, l, c),
                "c": c,
                "v": 1.0,
            },
            "recent_candles": recent_candles,
            "direction": action,
            "direction_probability": confidence,
        }

    def _scenario_forecast_for_tracker(
        self,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
    ) -> dict[str, Any]:
        tracked_candles = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], tracking_summary.get("tracked_candles", []))
            if isinstance(item, Mapping)
        ]
        if tracked_candles:
            closes = np.array([
                _float_or(row.get("c", row.get("close", tracking_summary.get("latest_price_proxy", 1.0))), 1.0)
                for row in tracked_candles[-24:]
            ], dtype=np.float32)
            highs = np.array([
                _float_or(row.get("h", row.get("high", closes[-1])), float(closes[-1]))
                for row in tracked_candles[-24:]
            ], dtype=np.float32)
            lows = np.array([
                _float_or(row.get("l", row.get("low", closes[-1])), float(closes[-1]))
                for row in tracked_candles[-24:]
            ], dtype=np.float32)
        else:
            close_value = _float_or(tracking_summary.get("latest_price_proxy", 1.0), 1.0)
            closes = np.array([close_value], dtype=np.float32)
            highs = np.array([close_value], dtype=np.float32)
            lows = np.array([close_value], dtype=np.float32)

        last_close = float(closes[-1])
        local_slope = _float_or(tracking_summary.get("local_slope", 0.0), 0.0)
        confidence = _clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", 0.5)))

        range_proxy = max(
            0.0005,
            float(np.max(highs) - np.min(lows)),
            float(np.std(closes)) * 2.0,
            abs(local_slope) * 2.5,
        )
        q50 = float(last_close + local_slope * 0.5)
        q05 = float(q50 - range_proxy * 0.35)
        q95 = float(q50 + range_proxy * 0.35)

        continuation = _clip01(tracking_summary.get("continuation_score", 0.5))
        pullback = _clip01(0.18 + (1.0 - continuation) * 0.32)
        reversal = _clip01(tracking_summary.get("reversal_score", 0.12))
        fakeout = _clip01(max(0.05, 1.0 - continuation - pullback - reversal))

        total_prob = max(1e-6, continuation + pullback + reversal + fakeout)
        continue_prob = continuation / total_prob
        pullback_prob = pullback / total_prob
        reversal_prob = reversal / total_prob
        fakeout_prob = fakeout / total_prob

        return {
            "q05": q05,
            "q50": q50,
            "q95": q95,
            "poly_slope": local_slope,
            "path_confidence": confidence,
            "continue_prob": continue_prob,
            "pullback_prob": pullback_prob,
            "reversal_attempt_prob": reversal_prob,
            "fakeout_prob": fakeout_prob,
            "structure_trade_ready": _clip01(1.0 if latest_signal.get("actionable", False) and confidence > 0.55 else 0.0),
            "interval": max(0.0001, range_proxy * 0.08),
        }

    def _build_tracker_scenario_analysis(
        self,
        *,
        tracking_summary: Mapping[str, Any],
        latest_signal: Mapping[str, Any],
        session_payload: Mapping[str, Any],
        controls: Mapping[str, Any],
    ) -> dict[str, Any]:
        enabled = bool(controls.get("scenario_generation_enabled", False))
        base: dict[str, Any] = {
            "enabled": enabled,
            "status": "disabled" if not enabled else "idle",
            "summary": "A* scenario generation is disabled.",
            "generated_at": _now_iso(),
            "total_scenarios": 0,
            "top_scenario": {},
            "scenarios": [],
            "overlay": {"confidence_heatmap": [], "tree_structure": {}},
        }
        if not enabled:
            return base

        chart_state = self._scenario_chart_state_for_tracker(tracking_summary, latest_signal)
        if not chart_state:
            base["status"] = "insufficient_data"
            base["summary"] = "Scenario generation skipped: no tracked candles are available yet."
            return base

        forecast_output = self._scenario_forecast_for_tracker(tracking_summary, latest_signal)
        dominant_memory_side = _upper_action(
            _mapping_to_dict(session_payload.get("execution_memory_projection", {})).get("dominant_side", "HOLD")
        )
        memory_recall = {
            "memory_alignment": _clip01(
                _mapping_to_dict(session_payload.get("execution_memory_projection", {})).get(
                    "memory_similarity",
                    latest_signal.get("memory_similarity", tracking_summary.get("memory_similarity", 0.5)),
                )
            ),
            "memory_labels": [dominant_memory_side] if dominant_memory_side in {"BUY", "SELL"} else [],
        }

        try:
            scenarios = predict_scenarios_from_chart_and_forecast(
                chart_state=chart_state,
                forecast_output=forecast_output,
                memory_recall=memory_recall,
                num_scenarios=5,
                max_depth=5,
            )
            ranked = rank_scenarios_by_ensemble_agreement(
                scenarios,
                ensemble_decision=_upper_action(latest_signal.get("action", "HOLD")),
                ensemble_confidence=_clip01(latest_signal.get("effective_confidence", latest_signal.get("confidence", 0.5))),
            )
            paint = scenarios_to_paint_layer(ranked, chart_state)
        except Exception as exc:
            LOGGER.exception("Scenario generation failed during tracker capture.")
            base["status"] = "error"
            base["summary"] = f"Scenario generation failed: {exc}"
            return base

        if not ranked:
            base["status"] = "insufficient_data"
            base["summary"] = "Scenario generation completed but returned no viable paths."
            return base

        top = ranked[0]
        top_last = top.scenario.last_candle()
        compact: list[dict[str, Any]] = []
        for scenario in ranked[:3]:
            compact.append(
                {
                    "rank": int(scenario.rank),
                    "direction": str(scenario.scenario.last_candle().direction),
                    "probability": float(scenario.probability),
                    "cost": float(scenario.scenario.cost),
                    "transition_type": str(scenario.scenario.transition_type.value),
                    "memory_alignment": float(scenario.scenario.memory_alignment),
                }
            )

        heatmap = paint.get("confidence_heatmap", [])
        tree = _mapping_to_dict(paint.get("tree_structure", {}))
        return {
            "enabled": True,
            "status": "ready",
            "summary": str(paint.get("summary", "Scenario generation ready.")),
            "generated_at": _now_iso(),
            "total_scenarios": len(ranked),
            "top_scenario": {
                "rank": int(top.rank),
                "direction": str(top_last.direction),
                "probability": float(top.probability),
                "cost": float(top.scenario.cost),
                "transition_type": str(top.scenario.transition_type.value),
                "memory_alignment": float(top.scenario.memory_alignment),
            },
            "scenarios": compact,
            "overlay": {
                "confidence_heatmap": heatmap,
                "tree_structure": tree,
                "heatmap_shape": [len(heatmap), int(tree.get("branches", 0) or 0)],
            },
        }

    def _focus_meta_covers_window(self, focus_meta: Mapping[str, Any], image_size: tuple[int, int]) -> bool:
        bbox = cast(Sequence[Any], focus_meta.get("pixel_bbox", []))
        if len(bbox) < 4:
            return False
        width = max(1, int(image_size[0]))
        height = max(1, int(image_size[1]))
        x0, y0, x1, y1 = [int(round(float(value))) for value in bbox[:4]]
        return (
            x0 <= max(3, int(round(width * 0.015)))
            and y0 <= max(3, int(round(height * 0.015)))
            and x1 >= width - max(3, int(round(width * 0.015)))
            and y1 >= height - max(3, int(round(height * 0.015)))
        )

    def _derive_study_surface(
        self,
        *,
        window_image: Image.Image,
        selected_surface: Image.Image,
        selected_focus_meta: Mapping[str, Any],
    ) -> tuple[Image.Image, dict[str, Any]]:
        focus_meta = dict(selected_focus_meta)
        focus_meta.setdefault("source", "manual_focus_surface")
        if not self._focus_meta_covers_window(selected_focus_meta, window_image.size):
            return selected_surface, focus_meta

        detector = getattr(self.tracking_adapter, "_detect_chart_bbox", None)
        if not callable(detector):
            return selected_surface, focus_meta
        try:
            detector_result = detector(window_image)
        except Exception:
            LOGGER.debug("Full-window chart-plane derivation failed.", exc_info=True)
            return selected_surface, focus_meta
        if not isinstance(detector_result, (list, tuple)) or len(detector_result) < 2:
            return selected_surface, focus_meta
        raw_bbox = detector_result[0]
        confidence = _float_or(detector_result[1], 0.0)

        try:
            bbox = _clip_bbox_to_image(window_image.size, raw_bbox)
        except Exception:
            return selected_surface, focus_meta
        x0, y0, x1, y1 = [int(value) for value in bbox[:4]]
        crop_width = max(1, x1 - x0)
        crop_height = max(1, y1 - y0)
        full_width = max(1, int(window_image.width))
        full_height = max(1, int(window_image.height))
        coverage = float((crop_width * crop_height) / max(1, full_width * full_height))
        if (
            _clip01(confidence) < 0.30
            or coverage < 0.10
            or crop_width < int(round(full_width * 0.24))
            or crop_height < int(round(full_height * 0.22))
        ):
            return selected_surface, focus_meta
        if crop_width >= int(round(full_width * 0.985)) and crop_height >= int(round(full_height * 0.985)):
            return selected_surface, focus_meta

        chart_focus_meta = _pixel_bbox_meta(window_image.size, bbox)
        chart_focus_meta.update(
            {
                "confidence": _clip01(confidence),
                "source": "auto_full_window_chart_plane",
                "relative_to": "window_capture",
                "selected_focus_region": dict(selected_focus_meta),
            }
        )
        return window_image.crop((x0, y0, x1, y1)).convert("RGB"), chart_focus_meta

    def _capture_and_analyze(self, session_id: str, *, force: bool = False) -> None:
        capture_started_at = time.monotonic()
        with self._lock:
            last_capture_time = float(self._last_capture_time.get(session_id, 0.0) or 0.0)
            if (
                not force
                and last_capture_time > 0.0
                and capture_started_at - last_capture_time < self._capture_rate_limit_sec
            ):
                LOGGER.debug(
                    "Capture skipped for %s (rate limited: %.1fms since last capture).",
                    session_id,
                    (capture_started_at - last_capture_time) * 1000.0,
                )
                return
            self._last_capture_time[session_id] = capture_started_at
        payload = self._require_session(session_id)
        manual_focus = _public_manual_focus_region(payload.get("manual_focus_region", {}))
        if not bool(manual_focus.get("enabled", False)):
            with self._lock:
                payload["tracking_enabled"] = False
                payload["status"] = "awaiting_focus"
                payload["tracking_summary"] = _default_tracking_summary(message="Awaiting locked broker focus.")
                payload["latest_signal"] = _default_signal(
                    message="Awaiting locked broker focus before live tracking can start.",
                    status="awaiting_focus",
                )
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            return

        descriptor = self._resolve_window_descriptor(payload)
        if descriptor is None:
            with self._lock:
                payload["status"] = "waiting_for_window"
                payload["tracking_summary"] = _default_tracking_summary(message="The locked broker window is not visible right now.")
                payload["latest_signal"] = _default_signal(
                    message="The locked broker window is not visible right now.",
                    status="waiting_for_window",
                )
                payload["last_error"] = "The locked broker window is not visible right now."
                payload["updated_at"] = _now_iso()
                self._save_session(payload)
            return

        window_image = self.capture_backend.capture_window(descriptor).convert("RGB")
        surface_image, focus_meta = _crop_normalized_bbox(window_image, cast(Sequence[Any], manual_focus.get("normalized_bbox", [])))
        study_surface_image, study_focus_meta = self._derive_study_surface(
            window_image=window_image,
            selected_surface=surface_image,
            selected_focus_meta=focus_meta,
        )
        study: TrackingStudy | None = None
        error_message = ""
        try:
            study = self.tracking_adapter.study(study_surface_image, session_payload=payload)
        except Exception as exc:
            LOGGER.exception("Tracker study failed for session %s.", session_id)
            error_message = f"Tracker study failed: {exc}"
        if study is not None:
            integrity_error = _study_plane_integrity_error(study, study_surface_image.size)
            if integrity_error:
                LOGGER.error(integrity_error)
                error_message = integrity_error
                study = None

        capture_count = int(payload.get("capture_count", 0) or 0) + 1
        frame_index = int(payload.get("frame_index", 0) or 0) + 1
        session_dir = self._session_dir(str(payload["session_id"]))
        artifact_dir = session_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{frame_index:06d}_{_surface_signature(study_surface_image)}"
        window_path = artifact_dir / f"{stem}_window.png"
        chart_path = artifact_dir / f"{stem}_chart.png"
        overlay_path = artifact_dir / f"{stem}_overlay.png"
        full_overlay_path = artifact_dir / f"{stem}_full_overlay.png"
        decision_path = artifact_dir / f"{stem}_decision.json"

        _encode_png(window_image, window_path)
        if study is None:
            _encode_png(study_surface_image, chart_path)
            _encode_png(study_surface_image, overlay_path)
            full_overlay_image = _compose_full_window_overlay(window_image, study_surface_image, study_focus_meta)
            _encode_png(full_overlay_image, full_overlay_path)
            tracking_summary = _default_tracking_summary(message=error_message or "Tracker study failed.")
            tracking_summary["chart_region"] = {
                **study_focus_meta,
                "relative_to": "window_capture",
            }
            tracking_summary["display_region"] = dict(tracking_summary["chart_region"])
            tracking_summary["focus_region"] = dict(study_focus_meta)
            if list(cast(Sequence[Any], study_focus_meta.get("pixel_bbox", []))) != list(cast(Sequence[Any], focus_meta.get("pixel_bbox", []))):
                tracking_summary["selected_focus_region"] = dict(focus_meta)
            tracking_summary["artifact_integrity"] = {
                "selected_plane": _image_dimensions_payload(surface_image),
                "study_plane": _image_dimensions_payload(study_surface_image),
                "chart": _image_dimensions_payload(study_surface_image),
                "overlay": _image_dimensions_payload(study_surface_image),
                "full_window": _image_dimensions_payload(window_image),
                "full_overlay": _image_dimensions_payload(full_overlay_image),
                "matches_selected_plane": study_surface_image.size == surface_image.size,
                "matches_study_plane": True,
                "source": "fallback_selected_plane",
            }
            latest_signal = _default_signal(
                message=error_message or "Tracker study failed.",
                status="error",
            )
        else:
            chart_image = study.chart_image.convert("RGB")
            overlay_image = study.overlay_image.convert("RGB")
            _encode_png(chart_image, chart_path)
            _encode_png(overlay_image, overlay_path)
            full_overlay_image = _compose_full_window_overlay(window_image, overlay_image, study_focus_meta)
            _encode_png(full_overlay_image, full_overlay_path)
            tracking_summary = dict(study.tracking_summary)
            tracking_summary["focus_region"] = dict(study_focus_meta)
            if list(cast(Sequence[Any], study_focus_meta.get("pixel_bbox", []))) != list(cast(Sequence[Any], focus_meta.get("pixel_bbox", []))):
                tracking_summary["selected_focus_region"] = dict(focus_meta)
            tracking_summary["artifact_integrity"] = {
                "selected_plane": _image_dimensions_payload(surface_image),
                "study_plane": _image_dimensions_payload(study_surface_image),
                "chart": _image_dimensions_payload(chart_image),
                "overlay": _image_dimensions_payload(overlay_image),
                "full_window": _image_dimensions_payload(window_image),
                "full_overlay": _image_dimensions_payload(full_overlay_image),
                "matches_selected_plane": study_surface_image.size == surface_image.size,
                "matches_study_plane": True,
                "source": "tracking_adapter",
            }
            latest_signal = dict(study.latest_signal)

        detected_market = _normalize_fx_market_candidate(
            latest_signal.get("market", tracking_summary.get("detected_market", payload.get("market", "")))
        )
        if detected_market:
            latest_signal["market"] = detected_market
            payload["market"] = detected_market
        else:
            latest_signal["market"] = ""
            latest_signal["market_confidence"] = 0.0
            payload["market"] = ""
        latest_signal["timestamp"] = _now_iso()
        broker_surface = _default_broker_surface_payload()
        broker_execution_state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
        execution_memory_projection: dict[str, Any] | None = None
        try:
            broker_surface, broker_execution_state, execution_memory_projection = self._evaluate_broker_execution(
                payload=payload,
                descriptor=descriptor,
                window_image=window_image,
                surface_image=surface_image,
                tracking_summary=tracking_summary,
                latest_signal=latest_signal,
            )
        except Exception as exc:
            LOGGER.exception("Broker execution evaluation failed for session %s.", session_id)
            broker_surface = self._read_broker_surface(
                window_image,
                source="full_window_gui",
                manual_focus_region=manual_focus,
            )
            broker_execution_state = _normalize_broker_execution_state(payload.get("broker_execution_state", {}))
            broker_execution_state["status"] = "error"
            broker_execution_state["message"] = f"Broker execution evaluation failed: {exc}"
            broker_execution_state["recent_log"] = self._append_execution_log(
                broker_execution_state,
                status="error",
                message=str(broker_execution_state["message"]),
            )
        detected_market = _normalize_fx_market_candidate(
            latest_signal.get("market", tracking_summary.get("detected_market", payload.get("market", "")))
        )
        if detected_market:
            latest_signal["market"] = detected_market
            payload["market"] = detected_market
        else:
            latest_signal["market"] = ""
            latest_signal["market_confidence"] = 0.0
            payload["market"] = ""
        tracking_summary["broker_surface"] = broker_surface
        tracking_summary["broker_execution_state"] = broker_execution_state
        latest_signal["broker_execution_state"] = broker_execution_state
        fresh_payload_for_controls = _mapping_to_dict(_read_json(self._session_path(str(payload["session_id"])), {}))
        if fresh_payload_for_controls:
            payload["execution_controls"] = _normalize_execution_controls(
                fresh_payload_for_controls.get("execution_controls", payload.get("execution_controls", {}))
            )
        controls = _normalize_execution_controls(payload.get("execution_controls", {}))
        scenario_analysis = self._build_tracker_scenario_analysis(
            tracking_summary=tracking_summary,
            latest_signal=latest_signal,
            session_payload=payload,
            controls=controls,
        )
        tracking_summary["scenario_analysis"] = scenario_analysis
        latest_signal["scenario_analysis"] = scenario_analysis
        decision_payload = {
            "session_id": str(payload["session_id"]),
            "captured_at": _now_iso(),
            "locked_window": dict(descriptor),
            "focus_region": study_focus_meta,
            "selected_focus_region": focus_meta,
            "tracking_summary": tracking_summary,
            "latest_signal": latest_signal,
            "broker_surface": broker_surface,
            "broker_execution_state": broker_execution_state,
            "scenario_analysis": scenario_analysis,
        }
        _write_json_atomic(decision_path, decision_payload)

        history: list[dict[str, Any]] = [
            dict(cast(Mapping[str, Any], item))
            for item in cast(Sequence[Any], payload.get("recent_studies", []))
            if isinstance(item, Mapping)
        ]
        history.insert(0, _study_entry(latest_signal, tracking_summary))
        history = history[:24]

        with self._lock:
            persisted_payload = _mapping_to_dict(_read_json(self._session_path(str(payload["session_id"])), {}))
            payload["execution_controls"] = _normalize_execution_controls(
                persisted_payload.get("execution_controls", payload.get("execution_controls", {}))
            )
            broker_execution_state = _preserve_newer_active_execution_state(
                broker_execution_state,
                _mapping_to_dict(persisted_payload.get("broker_execution_state", {})),
            )
            tracking_summary["broker_execution_state"] = broker_execution_state
            latest_signal["broker_execution_state"] = broker_execution_state
            payload["capture_count"] = capture_count
            payload["frame_index"] = frame_index
            payload["last_capture_at"] = _now_iso()
            payload["updated_at"] = _now_iso()
            payload["status"] = "running" if bool(payload.get("tracking_enabled", False)) else "ready"
            payload["locked_window"] = dict(descriptor)
            payload["locked_title"] = str(descriptor.get("title", "") or "")
            payload["last_frame_path"] = str(window_path)
            payload["last_window_path"] = str(window_path)
            payload["last_chart_path"] = str(chart_path)
            payload["last_full_overlay_path"] = str(full_overlay_path)
            payload["last_display_chart_path"] = str(overlay_path)
            payload["last_overlay_path"] = str(overlay_path)
            payload["last_decision_path"] = str(decision_path)
            payload["tracking_summary"] = tracking_summary
            payload["latest_signal"] = latest_signal
            payload["broker_surface"] = broker_surface
            payload["broker_execution_state"] = broker_execution_state
            payload["scenario_analysis"] = scenario_analysis
            if execution_memory_projection is not None:
                payload["execution_memory_projection"] = execution_memory_projection
            payload["memory_projection_predict"] = _mark_memory_projection_payload_stale(
                payload.get("memory_projection_predict", {}),
                mode="predict",
                frame_index=frame_index,
                chart_path=str(chart_path),
            )
            payload["memory_projection_future"] = _mark_memory_projection_payload_stale(
                payload.get("memory_projection_future", {}),
                mode="future",
                frame_index=frame_index,
                chart_path=str(chart_path),
            )
            payload["recent_studies"] = history
            payload["last_error"] = error_message
            self._save_session(payload)
            execution_last_result = _mapping_to_dict(broker_execution_state.get("last_result", {}))
            self._write_session_event_log(
                str(payload["session_id"]),
                "capture_evaluated",
                status=str(payload["status"]),
                capture_count=int(payload.get("capture_count", 0) or 0),
                frame_index=int(payload.get("frame_index", 0) or 0),
                signal_action=_upper_action(latest_signal.get("action", "HOLD")),
                execution_status=str(broker_execution_state.get("status", "") or ""),
                execution_side=_upper_action(broker_execution_state.get("side", "HOLD")),
                execution_lane=str(broker_execution_state.get("lane", "NONE") or "NONE"),
                execution_message=str(broker_execution_state.get("message", "") or ""),
                expiry_seconds=int(broker_execution_state.get("expiry_seconds", 0) or 0),
                controls_ready=bool(broker_surface.get("controls_ready", False)),
                retry_block_until=str(broker_execution_state.get("retry_block_until", "") or ""),
                last_result_status=str(execution_last_result.get("status", "") or ""),
                last_result_message=str(execution_last_result.get("message", "") or ""),
            )
        self._prune_session_artifacts(artifact_dir)

    def _resolve_window_descriptor(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        locked = _mapping_to_dict(payload.get("locked_window", {}))
        locked_hwnd = int(locked.get("hwnd", 0) or 0)
        query = str(payload.get("window_query", "Pocket Option") or "Pocket Option").strip()
        locked_title = str(
            payload.get("locked_title", "") or locked.get("title", "") or ""
        )
        locked_family = _browser_family(locked_title)

        windows = [
            dict(row)
            for row in self.capture_backend.list_windows(query)
            if _window_descriptor_is_capture_usable(row)
        ]
        if locked_hwnd > 0:
            for row in windows:
                if int(row.get("hwnd", 0) or 0) == locked_hwnd:
                    return dict(row)
            for row in self.capture_backend.list_windows(None):
                if int(row.get("hwnd", 0) or 0) == locked_hwnd:
                    if _window_descriptor_is_capture_usable(row):
                        return dict(row)
                    break
        if windows:
            return dict(windows[0])
        visible_windows = [
            dict(row)
            for row in self.capture_backend.list_windows(None)
            if _window_descriptor_is_capture_usable(row)
        ]
        for row in visible_windows:
            if _title_matches_window_query(row.get("title", ""), query):
                return dict(row)
        if locked_family:
            family_windows = [
                dict(row)
                for row in visible_windows
                if _browser_family(row.get("title", "")) == locked_family
                and not _title_has_any_token(row.get("title", ""), _WINDOW_REACQUIRE_BLOCK_TOKENS)
            ]
            family_windows.sort(
                key=lambda row: (
                    int(row.get("width", 0) or 0) * int(row.get("height", 0) or 0),
                    len(str(row.get("title", "") or "")),
                ),
                reverse=True,
            )
            if family_windows:
                return family_windows[0]
        return None

    def _activate_descriptor_for_execution(
        self,
        payload: Mapping[str, Any],
        descriptor: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        try:
            import os

            if os.name != "nt":
                return dict(descriptor) if descriptor else None
            import ctypes
        except Exception:
            return dict(descriptor) if descriptor else None

        locked = _mapping_to_dict(payload.get("locked_window", {}))
        hwnd = int((descriptor or {}).get("hwnd", 0) or locked.get("hwnd", 0) or 0)
        if hwnd <= 0:
            return dict(descriptor) if descriptor else None

        user32 = ctypes.windll.user32
        activated = PocketOptionBrokerExecutionBackend._activate_locked_window_for_click(user32, hwnd)  # pyright: ignore[reportPrivateUsage]
        if not activated:
            return dict(descriptor) if descriptor else None

        base = dict(descriptor or locked)
        base.update({key: value for key, value in activated.items() if value not in (None, "")})
        if not str(base.get("title", "") or "").strip():
            base["title"] = str((descriptor or locked).get("title", "") or payload.get("locked_title", "") or "")
        return base

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / _slugify(session_id, "session")

    def _session_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _load_session(self, session_id: str) -> dict[str, Any]:
        raw = _mapping_to_dict(_read_json(self._session_path(session_id), {}))
        if not raw:
            return {}
        normalized = self._normalize_session_payload(raw, session_id_hint=session_id)
        if normalized != raw:
            self._save_session(normalized)
        return normalized

    def _require_session(self, session_id: str) -> dict[str, Any]:
        payload = self._load_session(session_id)
        if not payload:
            raise KeyError(session_id)
        return payload

    def _save_session(self, payload: Mapping[str, Any]) -> None:
        session_id = str(payload.get("session_id", "") or "")
        if not session_id:
            raise ValueError("Session payload is missing a session_id.")
        session_path = self._session_path(session_id)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(session_path, payload)

    def _public_session_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        public = dict(payload)
        session_id = str(public.get("session_id", "") or "")
        public["manual_focus_region"] = _public_manual_focus_region(public.get("manual_focus_region", {}))
        public["event_log_path"] = str(self._event_log_path(session_id)) if session_id else ""
        public["focus_selector"] = _public_focus_selector_state(
            public.get("focus_selector", {}),
            supported=self.focus_selector_backend.is_supported(),
        )
        public["execution_controls"] = _normalize_execution_controls(public.get("execution_controls", {}))
        public["broker_surface"] = dict(_default_broker_surface_payload(), **_mapping_to_dict(public.get("broker_surface", {})))
        public["broker_execution_state"] = _normalize_broker_execution_state(public.get("broker_execution_state", {}))
        if bool(public.get("tracking_enabled", False)):
            public["status"] = str(public.get("status", "running") or "running")
        elif bool(public["manual_focus_region"].get("enabled", False)):
            public["status"] = str(public.get("status", "ready") or "ready")
        else:
            public["status"] = str(public.get("status", "awaiting_focus") or "awaiting_focus")
        next_capture_epoch = self._next_capture_epoch.get(session_id, 0.0)
        public["next_capture_in_sec"] = max(0.0, float(next_capture_epoch - _now_epoch())) if next_capture_epoch > 0 else 0.0
        capture_plan = self._adaptive_capture_interval_plan(public)
        public["effective_capture_interval_sec"] = float(
            capture_plan.get("interval_sec", public.get("capture_interval_sec", _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC))
            or _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC
        )
        public["adaptive_timer_reason"] = str(capture_plan.get("reason", "") or "")
        latest_signal = _mapping_to_dict(public.get("latest_signal", {}))
        tracking_summary = _mapping_to_dict(public.get("tracking_summary", {}))
        decision_kernel = _mapping_to_dict(
            latest_signal.get("decision_kernel", tracking_summary.get("decision_kernel", {}))
        )
        candidate_bias = _upper_action(latest_signal.get("major_bias", latest_signal.get("candidate_action", "HOLD")))
        if candidate_bias not in {"BUY", "SELL"}:
            for value in (
                latest_signal.get("dominant_side"),
                decision_kernel.get("dominant_side"),
                decision_kernel.get("next_candle_bias"),
                tracking_summary.get("global_direction"),
                tracking_summary.get("local_direction"),
                tracking_summary.get("impulse_direction"),
            ):
                resolved = _upper_action(value, fallback="HOLD")
                if resolved in {"BUY", "SELL"}:
                    candidate_bias = resolved
                    break
        if "signal_id" not in latest_signal or not str(latest_signal.get("signal_id", "") or "").strip():
            identity = str(latest_signal.get("timestamp", public.get("updated_at", "")) or public.get("updated_at", ""))
            identity_key = re.sub(r"[^A-Za-z0-9]+", "_", identity).strip("_")[:32] or "signal"
            latest_signal["signal_id"] = f"tracker_{session_id}_{int(public.get('frame_index', 0) or 0)}_{identity_key}"
        latest_signal["session_id"] = session_id
        for bias_key in ("major_bias", "bias_direction", "direction"):
            if _upper_action(latest_signal.get(bias_key), fallback="HOLD") not in {"BUY", "SELL"}:
                latest_signal[bias_key] = candidate_bias
        latest_signal.setdefault("tracking_summary", tracking_summary)
        latest_signal.setdefault("countdown_seconds", public["next_capture_in_sec"] or public["effective_capture_interval_sec"])
        latest_signal.setdefault("next_event_countdown", public["next_capture_in_sec"] or public["effective_capture_interval_sec"])
        latest_signal.setdefault("effective_capture_interval_sec", public["effective_capture_interval_sec"])
        latest_signal.setdefault("capture_interval_sec", public.get("capture_interval_sec", _TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC))
        latest_signal.setdefault("adaptive_timer_reason", public["adaptive_timer_reason"])
        public["latest_signal"] = latest_signal
        if not isinstance(public.get("recent_studies"), list):
            public["recent_studies"] = []
        public["memory_projection_predict"] = self._normalized_session_memory_projection(public, mode="predict")
        public["memory_projection_future"] = self._normalized_session_memory_projection(public, mode="future")
        active_mode = str(public.get("memory_projection_active_mode", "") or "").strip().lower()
        if active_mode not in {"predict", "future"}:
            active_mode = ""
        public["memory_projection_active_mode"] = active_mode
        if active_mode == "future":
            public["memory_projection_current"] = dict(public["memory_projection_future"])
        elif active_mode == "predict":
            public["memory_projection_current"] = dict(public["memory_projection_predict"])
        else:
            public["memory_projection_current"] = _default_memory_projection_payload(mode="predict")
        return public
