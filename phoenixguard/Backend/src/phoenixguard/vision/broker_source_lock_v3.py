from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, cast

import numpy as np
from PIL import Image


BROKER_SOURCE_LOCK_V3_SCHEMA_VERSION = "BROKER_SOURCE_LOCK_V3"
DASHBOARD_WINDOW_CAPTURE_GUARD_V3_SCHEMA_VERSION = "DASHBOARD_WINDOW_CAPTURE_GUARD_V3"


class BrokerSourceLockStatusV3(str, Enum):
    VALID = "VALID"
    BROKER_NOT_FOUND = "BROKER_NOT_FOUND"
    AMBIGUOUS_BROKER_TARGET = "AMBIGUOUS_BROKER_TARGET"
    TITLE_MATCH_PIXEL_MISMATCH = "TITLE_MATCH_PIXEL_MISMATCH"
    WRONG_SURFACE = "WRONG_SURFACE"
    INVALID_BROWSER = "INVALID_BROWSER"
    MISSING_TARGET_IDENTITY = "MISSING_TARGET_IDENTITY"
    VIEWPORT_MISMATCH = "VIEWPORT_MISMATCH"
    FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"


VALID = BrokerSourceLockStatusV3.VALID.value
BROKER_NOT_FOUND = BrokerSourceLockStatusV3.BROKER_NOT_FOUND.value
AMBIGUOUS_BROKER_TARGET = BrokerSourceLockStatusV3.AMBIGUOUS_BROKER_TARGET.value
TITLE_MATCH_PIXEL_MISMATCH = BrokerSourceLockStatusV3.TITLE_MATCH_PIXEL_MISMATCH.value
WRONG_SURFACE = BrokerSourceLockStatusV3.WRONG_SURFACE.value
INVALID_BROWSER = BrokerSourceLockStatusV3.INVALID_BROWSER.value
MISSING_TARGET_IDENTITY = BrokerSourceLockStatusV3.MISSING_TARGET_IDENTITY.value
VIEWPORT_MISMATCH = BrokerSourceLockStatusV3.VIEWPORT_MISMATCH.value
FINGERPRINT_MISMATCH = BrokerSourceLockStatusV3.FINGERPRINT_MISMATCH.value

BROKER_SOURCE_LOCK_STATUSES = frozenset(status.value for status in BrokerSourceLockStatusV3)


class WrongSurfaceClassV3(str, Enum):
    BROKER_SURFACE = "BROKER_SURFACE"
    PHOENIXGUARD_DASHBOARD = "PHOENIXGUARD_DASHBOARD"
    CHATGPT = "CHATGPT"
    VISUAL_STUDIO_CODE = "VISUAL_STUDIO_CODE"
    TERMINAL = "TERMINAL"
    WINDOWS_DESKTOP_TASKBAR = "WINDOWS_DESKTOP_TASKBAR"
    NON_BROKER_CAPTURE = "NON_BROKER_CAPTURE"
    UNKNOWN = "UNKNOWN"


BROKER_SURFACE = WrongSurfaceClassV3.BROKER_SURFACE.value
PHOENIXGUARD_DASHBOARD = WrongSurfaceClassV3.PHOENIXGUARD_DASHBOARD.value
CHATGPT = WrongSurfaceClassV3.CHATGPT.value
VISUAL_STUDIO_CODE = WrongSurfaceClassV3.VISUAL_STUDIO_CODE.value
TERMINAL = WrongSurfaceClassV3.TERMINAL.value
WINDOWS_DESKTOP_TASKBAR = WrongSurfaceClassV3.WINDOWS_DESKTOP_TASKBAR.value
NON_BROKER_CAPTURE = WrongSurfaceClassV3.NON_BROKER_CAPTURE.value
UNKNOWN = WrongSurfaceClassV3.UNKNOWN.value

WRONG_SURFACE_CLASSES = frozenset(
    {
        PHOENIXGUARD_DASHBOARD,
        CHATGPT,
        VISUAL_STUDIO_CODE,
        TERMINAL,
        WINDOWS_DESKTOP_TASKBAR,
        NON_BROKER_CAPTURE,
    }
)

DEFAULT_BROKER_TITLE_TOKENS = (
    "pocket option",
    "pocketoption",
    "the most innovative trading platform",
)
DEFAULT_BROKER_URL_TOKENS = (
    "pocketoption.com",
    "pocketoption",
)
DEFAULT_REQUIRED_BROWSER = "edge"
STUDY_SOURCE_ROLE_TOKENS = frozenset(
    {
        "study",
        "chart",
        "chart_study",
        "study_source",
        "chart_source",
        "visible_chart",
        "tradingview",
        "tradingview_chart",
    }
)
DEFAULT_MIN_VIEWPORT = (480, 320)
DEFAULT_MAX_VIEWPORT = (8192, 4320)
DEFAULT_VIEWPORT_TOLERANCE_PX = 6
DEFAULT_MAX_CANDIDATES = 16


def _empty_evidence() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class BrokerSourceTargetV3:
    browser: str = ""
    url: str = ""
    title: str = ""
    window_handle: str = ""
    target_id: str = ""
    viewport: tuple[int, int] = (0, 0)
    candidate_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "browser": self.browser,
            "url": self.url,
            "title": self.title,
            "window_handle": self.window_handle,
            "target_id": self.target_id,
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
            "candidate_id": self.candidate_id,
        }


@dataclass(frozen=True)
class DashboardWindowCaptureGuardV3:
    surface_class: str
    wrong_surface: bool
    broker_like_pixels: bool
    confidence: float
    reason: str
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    evidence: dict[str, object] = field(default_factory=_empty_evidence)

    @property
    def capture_safe(self) -> bool:
        return not self.wrong_surface

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": DASHBOARD_WINDOW_CAPTURE_GUARD_V3_SCHEMA_VERSION,
            "surface_class": self.surface_class,
            "wrong_surface": self.wrong_surface,
            "capture_safe": self.capture_safe,
            "broker_like_pixels": self.broker_like_pixels,
            "confidence": round(float(self.confidence), 4),
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "evidence": dict(self.evidence),
        }


WrongSurfaceClassificationV3 = DashboardWindowCaptureGuardV3


@dataclass(frozen=True)
class BrokerSourceLockV3:
    status: str
    valid: bool
    reason: str
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    selected_target: BrokerSourceTargetV3 | None = None
    candidate_count: int = 0
    matching_candidate_count: int = 0
    surface_guard: DashboardWindowCaptureGuardV3 = field(
        default_factory=lambda: DashboardWindowCaptureGuardV3(
            surface_class=UNKNOWN,
            wrong_surface=False,
            broker_like_pixels=False,
            confidence=0.0,
            reason="Surface was not classified.",
            reason_codes=("SURFACE_NOT_CLASSIFIED",),
        )
    )
    broker_pixel_fingerprint: str = ""
    broker_control_fingerprint: str = ""
    viewport_fingerprint: str = ""
    evidence: dict[str, object] = field(default_factory=_empty_evidence)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": BROKER_SOURCE_LOCK_V3_SCHEMA_VERSION,
            "status": self.status,
            "valid": self.valid,
            "broker_source_locked": self.valid,
            "reason": self.reason,
            "reason_codes": list(self.reason_codes),
            "selected_target": self.selected_target.as_dict() if self.selected_target else {},
            "candidate_count": self.candidate_count,
            "matching_candidate_count": self.matching_candidate_count,
            "surface_guard": self.surface_guard.as_dict(),
            "broker_pixel_fingerprint": self.broker_pixel_fingerprint,
            "broker_control_fingerprint": self.broker_control_fingerprint,
            "viewport_fingerprint": self.viewport_fingerprint,
            "evidence": dict(self.evidence),
        }


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[object], value))
    return []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _compact(value: Any) -> str:
    return "".join(ch for ch in _lower(value) if ch.isalnum())


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)
    return int(default) if parsed < 0 else parsed


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _token_sequence(value: Any, defaults: Sequence[str]) -> tuple[str, ...]:
    values = _sequence(value)
    if values:
        tokens = tuple(_lower(item) for item in values if _text(item))
    else:
        tokens = tuple(_lower(item) for item in defaults if _text(item))
    return tuple(dict.fromkeys(token for token in tokens if token))


def _expected_from(payload: Mapping[str, Any], expected: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = _mapping(payload.get("expected_broker_source_lock"))
    if not merged:
        merged = _mapping(payload.get("expected_broker_source"))
    if not merged:
        previous_lock = _mapping(payload.get("broker_source_lock"))
        if previous_lock.get("status") == VALID or previous_lock.get("selected_target"):
            merged = previous_lock
    if expected:
        merged.update(_mapping(expected))
    return merged


def _expected_browser(expected: Mapping[str, Any]) -> str:
    explicit = _first_text(expected.get("browser"), expected.get("required_browser"))
    return _lower(explicit) if explicit else DEFAULT_REQUIRED_BROWSER


def _browser_matches_required(browser: str, required_browser: str) -> bool:
    required = _lower(required_browser)
    if required in {"", "any", "*", "browser", "visible_browser"}:
        return True
    return bool(browser and browser == required)


def _expected_is_study_source(expected: Mapping[str, Any]) -> bool:
    role = _compact(
        _first_text(
            expected.get("source_role"),
            expected.get("surface_role"),
            expected.get("broker_role"),
            expected.get("mode"),
        )
    )
    source = _compact(
        _first_text(
            expected.get("source_kind"),
            expected.get("broker_kind"),
            expected.get("broker"),
            expected.get("platform"),
        )
    )
    return role in STUDY_SOURCE_ROLE_TOKENS or source in STUDY_SOURCE_ROLE_TOKENS


def _broker_title_tokens(expected: Mapping[str, Any]) -> tuple[str, ...]:
    return _token_sequence(
        expected.get("broker_title_tokens") or expected.get("title_tokens"),
        DEFAULT_BROKER_TITLE_TOKENS,
    )


def _broker_url_tokens(expected: Mapping[str, Any]) -> tuple[str, ...]:
    return _token_sequence(
        expected.get("broker_url_tokens") or expected.get("url_tokens"),
        DEFAULT_BROKER_URL_TOKENS,
    )


def _text_has_token(text: str, tokens: Sequence[str]) -> bool:
    lowered = _lower(text)
    compacted = _compact(text)
    for token in tokens:
        lowered_token = _lower(token)
        compact_token = _compact(token)
        if lowered_token and lowered_token in lowered:
            return True
        if compact_token and compact_token in compacted:
            return True
    return False


def _broker_text_match(row: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    title = _first_text(row.get("title"), row.get("window_title"), row.get("locked_title"))
    url = _first_text(row.get("url"), row.get("current_url"), row.get("target_url"), row.get("page_url"))
    title_matches = _text_has_token(title, _broker_title_tokens(expected))
    url_matches = _text_has_token(url, _broker_url_tokens(expected))
    return {
        "title": title,
        "url": url,
        "title_matches": title_matches,
        "url_matches": url_matches,
        "matches": title_matches or url_matches,
    }


def _browser_family(row: Mapping[str, Any]) -> str:
    raw = _first_text(
        row.get("browser"),
        row.get("browser_family"),
        row.get("browser_name"),
        row.get("app"),
        row.get("app_name"),
        row.get("process_name"),
        row.get("process"),
        row.get("exe"),
        row.get("executable"),
        row.get("path"),
        row.get("title"),
    )
    lowered = raw.lower()
    compacted = _compact(raw)
    if "microsoft edge" in lowered or "msedge" in compacted or compacted.endswith("edge"):
        return "edge"
    if "google chrome" in lowered or "chrome" in compacted:
        return "chrome"
    if "firefox" in lowered:
        return "firefox"
    if "opera" in lowered:
        return "opera"
    return _lower(row.get("browser"))


def _window_handle(row: Mapping[str, Any]) -> str:
    return _first_text(row.get("window_handle"), row.get("hwnd"), row.get("handle"), row.get("window_id"))


def _target_id(row: Mapping[str, Any]) -> str:
    return _first_text(
        row.get("devtools_target_id"),
        row.get("target_id"),
        row.get("cdp_target_id"),
        row.get("page_target_id"),
        row.get("id") if row.get("type") == "page" else "",
    )


def _viewport_from_rect(value: Any) -> tuple[int, int]:
    seq = _sequence(value)
    if len(seq) >= 4:
        left = _int(seq[0])
        top = _int(seq[1])
        right = _int(seq[2])
        bottom = _int(seq[3])
        width = max(0, right - left)
        height = max(0, bottom - top)
        return width, height
    row = _mapping(value)
    if all(key in row for key in ("left", "top", "right", "bottom")):
        width = max(0, _int(row.get("right")) - _int(row.get("left")))
        height = max(0, _int(row.get("bottom")) - _int(row.get("top")))
        return width, height
    return 0, 0


def _viewport_tuple(value: Any) -> tuple[int, int]:
    row = _mapping(value)
    if row:
        nested = _mapping(row.get("viewport")) or _mapping(row.get("capture_plane"))
        if nested:
            width = _int(nested.get("width") or nested.get("viewport_width") or nested.get("innerWidth"))
            height = _int(nested.get("height") or nested.get("viewport_height") or nested.get("innerHeight"))
            if width > 0 and height > 0:
                return width, height
        width = _int(row.get("width") or row.get("viewport_width") or row.get("innerWidth") or row.get("image_width"))
        height = _int(row.get("height") or row.get("viewport_height") or row.get("innerHeight") or row.get("image_height"))
        if width > 0 and height > 0:
            return width, height
        rect = row.get("window_rect") or row.get("rect") or row.get("bbox")
        width, height = _viewport_from_rect(rect)
        if width > 0 and height > 0:
            return width, height
    seq = _sequence(value)
    if len(seq) >= 2:
        width = _int(seq[0])
        height = _int(seq[1])
        if width > 0 and height > 0:
            return width, height
    return 0, 0


def _expected_viewport(expected: Mapping[str, Any]) -> tuple[int, int]:
    return _viewport_tuple(expected)


def _viewport_bounds(expected: Mapping[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
    min_width = _int(expected.get("min_viewport_width"), DEFAULT_MIN_VIEWPORT[0])
    min_height = _int(expected.get("min_viewport_height"), DEFAULT_MIN_VIEWPORT[1])
    max_width = _int(expected.get("max_viewport_width"), DEFAULT_MAX_VIEWPORT[0])
    max_height = _int(expected.get("max_viewport_height"), DEFAULT_MAX_VIEWPORT[1])
    return (min_width, min_height), (max_width, max_height)


def _viewport_valid(viewport: tuple[int, int], expected: Mapping[str, Any]) -> bool:
    (min_width, min_height), (max_width, max_height) = _viewport_bounds(expected)
    width, height = viewport
    if width < min_width or height < min_height:
        return False
    if width > max_width or height > max_height:
        return False
    wanted = _expected_viewport(expected)
    if wanted == (0, 0):
        return True
    tolerance = _int(expected.get("viewport_tolerance_px"), DEFAULT_VIEWPORT_TOLERANCE_PX)
    return abs(width - wanted[0]) <= tolerance and abs(height - wanted[1]) <= tolerance


def broker_viewport_fingerprint_v3(viewport: Sequence[Any] | Mapping[str, Any]) -> str:
    width, height = _viewport_tuple(viewport)
    return f"vp:{width}x{height}" if width > 0 and height > 0 else ""


def broker_pixel_fingerprint_v3(image: Image.Image | None) -> str:
    if image is None:
        return ""
    try:
        source = image.convert("RGB")
        sample = source.resize((64, 36), Image.Resampling.BILINEAR)
        payload = f"{source.width}x{source.height}:".encode("ascii") + sample.tobytes()
    except Exception:
        return ""
    return "px:" + hashlib.sha256(payload).hexdigest()[:20]


def _normalized_box(value: Any) -> list[int]:
    seq = _sequence(value)
    if len(seq) < 4:
        return []
    left = _int(seq[0])
    top = _int(seq[1])
    right = _int(seq[2])
    bottom = _int(seq[3])
    if right <= left or bottom <= top:
        return []
    return [left, top, right, bottom]


def _control_box(row: Mapping[str, Any]) -> list[int]:
    return _normalized_box(row.get("bbox") or row.get("box") or row.get("rect") or row.get("pixel_bbox"))


def broker_control_fingerprint_v3(value: Mapping[str, Any] | None) -> str:
    row = _mapping(value)
    if not row:
        return ""
    visibility = _mapping(row.get("control_visibility"))
    boxes: dict[str, Any] = {}
    for key in ("buy_button", "sell_button", "amount_field", "time_field", "order_panel"):
        box = _control_box(_mapping(row.get(key)))
        if box:
            boxes[key] = box
    execution_boxes = _mapping(row.get("execution_boxes"))
    if execution_boxes:
        boxes["execution_boxes"] = {
            str(key): _control_box(_mapping(item))
            for key, item in sorted(execution_boxes.items(), key=lambda pair: str(pair[0]))
            if _control_box(_mapping(item))
        }
    descriptor: dict[str, object] = {
        "controls_ready": _bool(row.get("controls_ready")),
        "all_required_visible": _bool(visibility.get("all_required_visible")),
        "image_width": _int(visibility.get("image_width") or _mapping(row.get("capture_plane")).get("width")),
        "image_height": _int(visibility.get("image_height") or _mapping(row.get("capture_plane")).get("height")),
        "boxes": boxes,
    }
    if not descriptor["controls_ready"] and not descriptor["all_required_visible"] and not boxes:
        return ""
    encoded = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "ctrl:" + hashlib.sha256(encoded).hexdigest()[:20]


def _control_source(payload: Mapping[str, Any], selected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected_row = _mapping(selected)
    for source in (
        selected_row.get("broker_surface"),
        selected_row.get("broker_controls"),
        payload.get("broker_surface"),
        payload.get("broker_controls"),
        payload.get("broker_execution_state"),
        selected_row,
        payload,
    ):
        row = _mapping(source)
        if row.get("broker_surface") and source is payload.get("broker_execution_state"):
            row = _mapping(row.get("broker_surface"))
        if any(key in row for key in ("controls_ready", "buy_button", "sell_button", "control_visibility", "execution_boxes")):
            return row
    return {}


def _controls_ready(value: Mapping[str, Any]) -> bool:
    row = _mapping(value)
    visibility = _mapping(row.get("control_visibility"))
    if _bool(row.get("controls_ready")) or _bool(visibility.get("all_required_visible")):
        return True
    buy_box = _control_box(_mapping(row.get("buy_button")))
    sell_box = _control_box(_mapping(row.get("sell_button")))
    return bool(buy_box and sell_box)


def _broker_button_evidence(image: Image.Image | None) -> dict[str, Any]:
    if image is None:
        return {
            "image_present": False,
            "broker_like_pixels": False,
            "green_pixels": 0,
            "red_pixels": 0,
            "minimum_pixels": 0,
            "panel_area": 0,
            "green_ratio": 0.0,
            "red_ratio": 0.0,
        }
    try:
        width, height = image.size
    except Exception:
        width, height = 0, 0
    if width < DEFAULT_MIN_VIEWPORT[0] or height < DEFAULT_MIN_VIEWPORT[1]:
        return {
            "image_present": True,
            "broker_like_pixels": False,
            "image_width": width,
            "image_height": height,
            "green_pixels": 0,
            "red_pixels": 0,
            "minimum_pixels": 0,
            "panel_area": 0,
            "green_ratio": 0.0,
            "red_ratio": 0.0,
            "reason": "IMAGE_BELOW_MIN_VIEWPORT",
        }
    try:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    except Exception:
        return {
            "image_present": True,
            "broker_like_pixels": False,
            "image_width": width,
            "image_height": height,
            "green_pixels": 0,
            "red_pixels": 0,
            "minimum_pixels": 0,
            "panel_area": 0,
            "green_ratio": 0.0,
            "red_ratio": 0.0,
            "reason": "IMAGE_ARRAY_UNREADABLE",
        }

    y0 = max(0, int(round(height * 0.22)))
    y1 = min(height, int(round(height * 0.70)))
    x0 = max(0, int(round(width * 0.76)))
    x1 = min(width, int(round(width * 0.98)))
    panel = arr[y0:y1, x0:x1, :3].astype(np.int16, copy=False)
    if panel.size == 0:
        area = 0
        green_pixels = 0
        red_pixels = 0
    else:
        red = panel[:, :, 0]
        green = panel[:, :, 1]
        blue = panel[:, :, 2]
        green_pixels = int(
            np.count_nonzero(
                (green >= 120)
                & (red <= 125)
                & (blue <= 155)
                & ((green - red) >= 42)
                & ((green - blue) >= 22)
            )
        )
        red_pixels = int(
            np.count_nonzero(
                (red >= 160)
                & (green <= 135)
                & (blue <= 145)
                & ((red - green) >= 42)
            )
        )
        area = max(1, int(panel.shape[0] * panel.shape[1]))
    minimum_pixels = max(240, int(round(area * 0.0035))) if area else 240
    broker_like = bool(green_pixels >= minimum_pixels and red_pixels >= minimum_pixels)
    return {
        "image_present": True,
        "broker_like_pixels": broker_like,
        "image_width": width,
        "image_height": height,
        "green_pixels": green_pixels,
        "red_pixels": red_pixels,
        "minimum_pixels": minimum_pixels,
        "panel_area": area,
        "green_ratio": round(float(green_pixels / max(1, area)), 6),
        "red_ratio": round(float(red_pixels / max(1, area)), 6),
        "right_panel_bbox": [x0, y0, x1, y1],
    }


def looks_like_pocket_option_broker_surface_v3(image: Image.Image | None) -> bool:
    return bool(_broker_button_evidence(image).get("broker_like_pixels", False))


def _chart_source_evidence(image: Image.Image | None) -> dict[str, Any]:
    if image is None:
        return {
            "image_present": False,
            "chart_like_pixels": False,
            "candle_pixels": 0,
            "minimum_pixels": 0,
            "chart_area": 0,
            "candle_ratio": 0.0,
        }
    try:
        width, height = image.size
    except Exception:
        width, height = 0, 0
    if width < DEFAULT_MIN_VIEWPORT[0] or height < DEFAULT_MIN_VIEWPORT[1]:
        return {
            "image_present": True,
            "chart_like_pixels": False,
            "image_width": width,
            "image_height": height,
            "candle_pixels": 0,
            "minimum_pixels": 0,
            "chart_area": 0,
            "candle_ratio": 0.0,
            "reason": "IMAGE_BELOW_MIN_VIEWPORT",
        }
    try:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
    except Exception:
        return {
            "image_present": True,
            "chart_like_pixels": False,
            "image_width": width,
            "image_height": height,
            "candle_pixels": 0,
            "minimum_pixels": 0,
            "chart_area": 0,
            "candle_ratio": 0.0,
            "reason": "IMAGE_ARRAY_UNREADABLE",
        }

    y0 = max(0, int(round(height * 0.12)))
    y1 = min(height, int(round(height * 0.94)))
    x0 = max(0, int(round(width * 0.04)))
    x1 = min(width, int(round(width * 0.96)))
    plane = arr[y0:y1, x0:x1, :3].astype(np.int16, copy=False)
    if plane.size == 0:
        area = 0
        candle_pixels = 0
    else:
        red = plane[:, :, 0]
        green = plane[:, :, 1]
        blue = plane[:, :, 2]
        green_candles = (
            (green >= 125)
            & (red <= 155)
            & (blue <= 170)
            & ((green - red) >= 35)
            & ((green - blue) >= 12)
        )
        red_candles = (
            (red >= 150)
            & (green <= 145)
            & (blue <= 155)
            & ((red - green) >= 30)
        )
        magenta_candles = (
            (red >= 165)
            & (blue >= 135)
            & (green <= 135)
            & ((red - green) >= 35)
            & ((blue - green) >= 25)
        )
        candle_pixels = int(np.count_nonzero(green_candles | red_candles | magenta_candles))
        area = max(1, int(plane.shape[0] * plane.shape[1]))
    minimum_pixels = max(90, int(round(area * 0.00035))) if area else 90
    chart_like = bool(candle_pixels >= minimum_pixels)
    return {
        "image_present": True,
        "chart_like_pixels": chart_like,
        "image_width": width,
        "image_height": height,
        "candle_pixels": candle_pixels,
        "minimum_pixels": minimum_pixels,
        "chart_area": area,
        "candle_ratio": round(float(candle_pixels / max(1, area)), 6),
        "chart_bbox": [x0, y0, x1, y1],
    }


def _desktop_taskbar_image_evidence(image: Image.Image | None) -> dict[str, Any]:
    if image is None:
        return {"image_present": False, "taskbar_dominant": False, "taskbar_ratio": 0.0, "desktop_uniformity": 0.0}
    try:
        stats_image = image.convert("RGB")
        width, height = stats_image.size
        max_width = 640
        max_height = 360
        if width > max_width or height > max_height:
            scale = min(max_width / max(1, width), max_height / max(1, height))
            sample_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
            stats_image = stats_image.resize(sample_size)
        arr = np.asarray(stats_image, dtype=np.uint8)
    except Exception:
        return {"image_present": True, "taskbar_dominant": False, "taskbar_ratio": 0.0, "desktop_uniformity": 0.0}
    if arr.ndim != 3 or arr.shape[0] < 80 or arr.shape[1] < 120:
        return {"image_present": True, "taskbar_dominant": False, "taskbar_ratio": 0.0, "desktop_uniformity": 0.0}
    height = int(arr.shape[0])
    taskbar_h = max(24, int(round(height * 0.075)))
    bottom = arr[height - taskbar_h :, :, :3].astype(np.float32)
    body = arr[: height - taskbar_h, :, :3].astype(np.float32)
    bottom_mean = bottom.mean(axis=(0, 1))
    body_std = float(body.std(axis=(0, 1)).mean()) if body.size else 0.0
    bottom_std = float(bottom.std(axis=(0, 1)).mean()) if bottom.size else 0.0
    dark_bottom = bool(float(bottom_mean.mean()) < 75.0)
    uniform_body = bool(body_std < 18.0)
    uniform_bottom = bool(bottom_std < 22.0)
    taskbar_dominant = bool(dark_bottom and uniform_bottom and uniform_body)
    return {
        "image_present": True,
        "taskbar_dominant": taskbar_dominant,
        "taskbar_ratio": round(float(taskbar_h / max(1, height)), 4),
        "desktop_uniformity": round(float(max(0.0, 1.0 - body_std / 64.0)), 4),
        "taskbar_uniformity": round(float(max(0.0, 1.0 - bottom_std / 64.0)), 4),
        "taskbar_mean_rgb": [round(float(item), 2) for item in bottom_mean.tolist()],
    }


def _surface_text(payload: Mapping[str, Any]) -> str:
    locked = _mapping(payload.get("locked_window"))
    descriptor = _mapping(payload.get("window_descriptor"))
    parts: list[object] = [
        payload.get("title"),
        payload.get("window_title"),
        payload.get("locked_title"),
        locked.get("title"),
        descriptor.get("title"),
        payload.get("url"),
        payload.get("current_url"),
        payload.get("target_url"),
        payload.get("app"),
        payload.get("app_name"),
        payload.get("process_name"),
        payload.get("process"),
        payload.get("exe"),
        payload.get("executable"),
        payload.get("class_name"),
        payload.get("window_class"),
        payload.get("ocr_text"),
        payload.get("visible_text"),
        payload.get("caption"),
    ]
    return " ".join(_text(part) for part in parts if _text(part))


def classify_wrong_surface_v3(
    payload: Mapping[str, Any] | None = None,
    *,
    image: Image.Image | None = None,
    expected: Mapping[str, Any] | None = None,
) -> DashboardWindowCaptureGuardV3:
    row = _mapping(payload)
    expected_row = _expected_from(row, expected)
    surface_text = _surface_text(row)
    compacted = _compact(surface_text)
    broker_text = _broker_text_match(row, expected_row)
    pixel_evidence = _broker_button_evidence(image)
    chart_evidence = _chart_source_evidence(image)
    desktop_evidence = _desktop_taskbar_image_evidence(image)
    control_source = _control_source(row)
    controls_ready = _controls_ready(control_source)
    broker_like_pixels = bool(pixel_evidence.get("broker_like_pixels", False))
    chart_like_pixels = bool(chart_evidence.get("chart_like_pixels", False))
    study_source_expected = _expected_is_study_source(expected_row)
    evidence: dict[str, object] = {
        "title": broker_text["title"],
        "url": broker_text["url"],
        "broker_title_matches": broker_text["title_matches"],
        "broker_url_matches": broker_text["url_matches"],
        "study_source_expected": study_source_expected,
        "controls_ready": controls_ready,
        "surface_text_compact": compacted[:160],
        "pixel": pixel_evidence,
        "chart": chart_evidence,
        "desktop": desktop_evidence,
    }

    if broker_like_pixels:
        return DashboardWindowCaptureGuardV3(
            surface_class=BROKER_SURFACE,
            wrong_surface=False,
            broker_like_pixels=True,
            confidence=0.92 if broker_text["matches"] else 0.76,
            reason="Captured pixels include broker BUY/SELL controls.",
            reason_codes=("BROKER_PIXELS_CONFIRMED",),
            evidence=evidence,
        )
    if study_source_expected and broker_text["matches"] and chart_like_pixels:
        return DashboardWindowCaptureGuardV3(
            surface_class=BROKER_SURFACE,
            wrong_surface=False,
            broker_like_pixels=False,
            confidence=0.84,
            reason="Captured pixels include a chart study surface with visible candle evidence.",
            reason_codes=("CHART_SOURCE_PIXELS_CONFIRMED",),
            evidence=evidence,
        )
    if broker_text["matches"] and image is None:
        if controls_ready:
            return DashboardWindowCaptureGuardV3(
                surface_class=BROKER_SURFACE,
                wrong_surface=False,
                broker_like_pixels=False,
                confidence=0.82,
                reason="Broker title or URL matched and broker controls are present in payload evidence.",
                reason_codes=("BROKER_CONTROLS_CONFIRMED",),
                evidence=evidence,
            )
        return DashboardWindowCaptureGuardV3(
            surface_class=UNKNOWN,
            wrong_surface=False,
            broker_like_pixels=False,
            confidence=0.45,
            reason="Broker title or URL matched, but no screenshot was supplied for pixel classification.",
            reason_codes=("BROKER_TEXT_MATCHED_NO_IMAGE",),
            evidence=evidence,
        )
    if study_source_expected and broker_text["matches"]:
        return DashboardWindowCaptureGuardV3(
            surface_class=NON_BROKER_CAPTURE,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.80,
            reason="Study-source title or URL matched, but chart candle pixels were not detected.",
            reason_codes=("CHART_SOURCE_TEXT_PIXEL_MISMATCH",),
            evidence=evidence,
        )
    if broker_text["matches"]:
        return DashboardWindowCaptureGuardV3(
            surface_class=NON_BROKER_CAPTURE,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.86,
            reason="Broker title or URL matched, but broker control pixels were not detected.",
            reason_codes=("BROKER_TEXT_PIXEL_MISMATCH",),
            evidence=evidence,
        )

    if "phoenixguard" in compacted or "pocketlive8788" in compacted or "windowtrackerdashboard" in compacted:
        return DashboardWindowCaptureGuardV3(
            surface_class=PHOENIXGUARD_DASHBOARD,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.94,
            reason="Capture appears to be the PhoenixGuard dashboard, not the broker.",
            reason_codes=("PHOENIXGUARD_DASHBOARD_CAPTURED",),
            evidence=evidence,
        )
    if "chatgpt" in compacted or "chatopenai" in compacted or "openaicomc" in compacted:
        return DashboardWindowCaptureGuardV3(
            surface_class=CHATGPT,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.94,
            reason="Capture appears to be ChatGPT, not the broker.",
            reason_codes=("CHATGPT_CAPTURED",),
            evidence=evidence,
        )
    if "visualstudiocode" in compacted or "codeexe" in compacted or "vscode" in compacted:
        return DashboardWindowCaptureGuardV3(
            surface_class=VISUAL_STUDIO_CODE,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.94,
            reason="Capture appears to be Visual Studio Code, not the broker.",
            reason_codes=("VISUAL_STUDIO_CODE_CAPTURED",),
            evidence=evidence,
        )
    if (
        "windowsterminal" in compacted
        or "powershell" in compacted
        or "pwsh" in compacted
        or "commandprompt" in compacted
        or "cmdexe" in compacted
        or "conhost" in compacted
        or compacted.endswith("terminal")
    ):
        return DashboardWindowCaptureGuardV3(
            surface_class=TERMINAL,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.93,
            reason="Capture appears to be a terminal, not the broker.",
            reason_codes=("TERMINAL_CAPTURED",),
            evidence=evidence,
        )
    if (
        "shelltraywnd" in compacted
        or "workerw" in compacted
        or "progman" in compacted
        or "programmanager" in compacted
        or "taskbar" in compacted
        or bool(desktop_evidence.get("taskbar_dominant", False))
    ):
        return DashboardWindowCaptureGuardV3(
            surface_class=WINDOWS_DESKTOP_TASKBAR,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.91,
            reason="Capture appears dominated by the Windows desktop/taskbar, not the broker.",
            reason_codes=("WINDOWS_DESKTOP_TASKBAR_CAPTURED",),
            evidence=evidence,
        )
    if surface_text or image is not None:
        return DashboardWindowCaptureGuardV3(
            surface_class=NON_BROKER_CAPTURE,
            wrong_surface=True,
            broker_like_pixels=False,
            confidence=0.62,
            reason="Capture does not match the broker title, URL, or control pixels.",
            reason_codes=("NON_BROKER_CAPTURE",),
            evidence=evidence,
        )
    return DashboardWindowCaptureGuardV3(
        surface_class=UNKNOWN,
        wrong_surface=False,
        broker_like_pixels=False,
        confidence=0.0,
        reason="No title, URL, payload text, or image was supplied for surface classification.",
        reason_codes=("INSUFFICIENT_SURFACE_EVIDENCE",),
        evidence=evidence,
    )


def _merge_candidate(payload: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(candidate)
    for key in (
        "browser",
        "browser_family",
        "url",
        "current_url",
        "target_url",
        "title",
        "window_title",
        "locked_title",
        "viewport",
        "width",
        "height",
        "viewport_width",
        "viewport_height",
        "window_rect",
        "broker_surface",
        "broker_controls",
    ):
        if key not in merged or merged.get(key) in (None, ""):
            if key in payload:
                merged[key] = payload.get(key)
    return merged


def _has_candidate_shape(row: Mapping[str, Any]) -> bool:
    return bool(
        _first_text(row.get("title"), row.get("window_title"), row.get("url"), row.get("current_url"), row.get("target_url"))
        or _window_handle(row)
        or _target_id(row)
        or _browser_family(row)
    )


def _candidate_rows(payload: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if candidates is not None:
        rows.extend(_merge_candidate(payload, item) for item in candidates)
    for key in ("broker_targets", "candidate_targets", "targets", "windows", "window_candidates", "candidates"):
        for item in _sequence(payload.get(key)):
            nested_item = _mapping(item)
            if nested_item:
                rows.append(_merge_candidate(payload, nested_item))
    for key in ("locked_window", "window_descriptor", "target", "devtools_target"):
        nested = _mapping(payload.get(key))
        if nested:
            rows.append(_merge_candidate(payload, nested))
    if _has_candidate_shape(payload):
        rows.append(dict(payload))

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        candidate_id = _candidate_identity(row)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        deduped.append(row)
        if len(deduped) >= DEFAULT_MAX_CANDIDATES:
            break
    return deduped


def _candidate_identity(row: Mapping[str, Any]) -> str:
    handle = _window_handle(row)
    target = _target_id(row)
    if handle:
        return f"hwnd:{handle}"
    if target:
        return f"target:{target}"
    descriptor: dict[str, object] = {
        "title": _first_text(row.get("title"), row.get("window_title"), row.get("locked_title")),
        "url": _first_text(row.get("url"), row.get("current_url"), row.get("target_url")),
        "viewport": _viewport_tuple(row),
    }
    encoded = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "candidate:" + hashlib.sha256(encoded).hexdigest()[:12]


def _target_from_row(row: Mapping[str, Any], expected: Mapping[str, Any]) -> BrokerSourceTargetV3:
    viewport = _viewport_tuple(row)
    return BrokerSourceTargetV3(
        browser=_browser_family(row),
        url=_first_text(row.get("url"), row.get("current_url"), row.get("target_url"), row.get("page_url")),
        title=_first_text(row.get("title"), row.get("window_title"), row.get("locked_title")),
        window_handle=_window_handle(row),
        target_id=_target_id(row),
        viewport=viewport,
        candidate_id=_candidate_identity(row),
    )


def _expected_identity_match(row: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    expected_handle = _first_text(expected.get("window_handle"), expected.get("hwnd"), expected.get("handle"))
    expected_target = _first_text(expected.get("devtools_target_id"), expected.get("target_id"), expected.get("cdp_target_id"))
    if expected_handle and _window_handle(row) != expected_handle:
        return False
    if expected_target and _target_id(row) != expected_target:
        return False
    return True


def _expected_pixel_fingerprint(expected: Mapping[str, Any]) -> str:
    return _first_text(
        expected.get("broker_pixel_fingerprint"),
        expected.get("pixel_fingerprint"),
        _mapping(expected.get("fingerprints")).get("broker_pixel_fingerprint"),
        _mapping(expected.get("fingerprints")).get("pixel"),
    )


def _expected_control_fingerprint(expected: Mapping[str, Any]) -> str:
    return _first_text(
        expected.get("broker_control_fingerprint"),
        expected.get("control_fingerprint"),
        _mapping(expected.get("fingerprints")).get("broker_control_fingerprint"),
        _mapping(expected.get("fingerprints")).get("control"),
    )


def _lock_result(
    status: str,
    reason: str,
    reason_codes: Sequence[str],
    *,
    selected_target: BrokerSourceTargetV3 | None = None,
    candidate_count: int = 0,
    matching_candidate_count: int = 0,
    surface_guard: DashboardWindowCaptureGuardV3,
    broker_pixel_fingerprint: str = "",
    broker_control_fingerprint: str = "",
    viewport_fingerprint: str = "",
    evidence: Mapping[str, object] | None = None,
) -> BrokerSourceLockV3:
    return BrokerSourceLockV3(
        status=status,
        valid=status == VALID,
        reason=reason,
        reason_codes=tuple(str(code) for code in reason_codes if str(code)),
        selected_target=selected_target,
        candidate_count=int(candidate_count),
        matching_candidate_count=int(matching_candidate_count),
        surface_guard=surface_guard,
        broker_pixel_fingerprint=broker_pixel_fingerprint,
        broker_control_fingerprint=broker_control_fingerprint,
        viewport_fingerprint=viewport_fingerprint,
        evidence=dict(evidence or {}),
    )


def evaluate_broker_source_lock_v3(
    payload: Mapping[str, Any] | None = None,
    *,
    image: Image.Image | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    expected: Mapping[str, Any] | None = None,
) -> BrokerSourceLockV3:
    row = _mapping(payload)
    expected_row = _expected_from(row, expected)
    guard = classify_wrong_surface_v3(row, image=image, expected=expected_row)
    pixel_fp = broker_pixel_fingerprint_v3(image)
    study_source_expected = _expected_is_study_source(expected_row)
    chart_evidence = _chart_source_evidence(image)
    chart_source_like = bool(study_source_expected and chart_evidence.get("chart_like_pixels", False))
    candidate_rows = _candidate_rows(row, candidates)
    required_browser = _expected_browser(expected_row)
    candidate_limit_exceeded = len(candidate_rows) >= DEFAULT_MAX_CANDIDATES

    text_matched_rows: list[dict[str, Any]] = []
    browser_matched_rows: list[dict[str, Any]] = []
    candidate_evidence: list[dict[str, object]] = []
    for candidate in candidate_rows:
        text_match = _broker_text_match(candidate, expected_row)
        browser = _browser_family(candidate)
        browser_ok = _browser_matches_required(browser, required_browser)
        evidence_row: dict[str, object] = {
            "candidate_id": _candidate_identity(candidate),
            "browser": browser,
            "browser_ok": browser_ok,
            "title": text_match["title"],
            "url": text_match["url"],
            "title_matches": text_match["title_matches"],
            "url_matches": text_match["url_matches"],
            "text_matches": text_match["matches"],
            "window_handle": _window_handle(candidate),
            "target_id": _target_id(candidate),
            "viewport": _viewport_tuple(candidate),
        }
        candidate_evidence.append(evidence_row)
        if not text_match["matches"]:
            continue
        text_matched_rows.append(candidate)
        if browser_ok:
            browser_matched_rows.append(candidate)

    base_evidence: dict[str, object] = {
        "required_browser": required_browser,
        "study_source_expected": study_source_expected,
        "chart_source_like": chart_source_like,
        "chart": chart_evidence,
        "candidate_limit_exceeded": candidate_limit_exceeded,
        "candidates": candidate_evidence,
        "surface_class": guard.surface_class,
        "surface_wrong": guard.wrong_surface,
    }

    if not candidate_rows:
        if guard.wrong_surface:
            return _lock_result(
                WRONG_SURFACE,
                guard.reason,
                guard.reason_codes or ("WRONG_SURFACE_CAPTURED",),
                candidate_count=0,
                matching_candidate_count=0,
                surface_guard=guard,
                broker_pixel_fingerprint=pixel_fp,
                evidence=base_evidence,
            )
        return _lock_result(
            BROKER_NOT_FOUND,
            "No browser candidates were supplied for broker-source locking.",
            ("NO_CANDIDATES",),
            candidate_count=0,
            matching_candidate_count=0,
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            evidence=base_evidence,
        )

    if text_matched_rows and not browser_matched_rows:
        browser_label = "the required browser" if required_browser in {"", "any", "*"} else f"the required {required_browser} browser"
        browser_reason_codes = (
            ("EDGE_BROWSER_REQUIRED",)
            if required_browser == DEFAULT_REQUIRED_BROWSER
            else ("BROWSER_REQUIREMENT_NOT_MET",)
        )
        return _lock_result(
            INVALID_BROWSER,
            f"A broker title or URL was found, but it was not in {browser_label} surface.",
            browser_reason_codes,
            candidate_count=len(candidate_rows),
            matching_candidate_count=0,
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            evidence=base_evidence,
        )

    if not browser_matched_rows:
        if guard.wrong_surface:
            return _lock_result(
                WRONG_SURFACE,
                guard.reason,
                guard.reason_codes or ("WRONG_SURFACE_CAPTURED",),
                candidate_count=len(candidate_rows),
                matching_candidate_count=0,
                surface_guard=guard,
                broker_pixel_fingerprint=pixel_fp,
                evidence=base_evidence,
            )
        return _lock_result(
            BROKER_NOT_FOUND,
            "No Edge browser target matched the broker title or URL tokens.",
            ("BROKER_TARGET_TEXT_NOT_FOUND",),
            candidate_count=len(candidate_rows),
            matching_candidate_count=0,
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            evidence=base_evidence,
        )

    identity_matched_rows = [candidate for candidate in browser_matched_rows if _expected_identity_match(candidate, expected_row)]
    if not identity_matched_rows:
        return _lock_result(
            BROKER_NOT_FOUND,
            "Broker target was found, but it did not match the expected window handle or DevTools target id.",
            ("EXPECTED_TARGET_IDENTITY_NOT_FOUND",),
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(browser_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            evidence=base_evidence,
        )

    unique_identities = {_candidate_identity(candidate) for candidate in identity_matched_rows}
    expected_has_identity = bool(
        _first_text(expected_row.get("window_handle"), expected_row.get("hwnd"), expected_row.get("handle"))
        or _first_text(expected_row.get("devtools_target_id"), expected_row.get("target_id"), expected_row.get("cdp_target_id"))
    )
    if len(unique_identities) > 1 and not expected_has_identity:
        return _lock_result(
            AMBIGUOUS_BROKER_TARGET,
            "Multiple Edge broker targets matched; a window handle or DevTools target id is required to lock one.",
            ("MULTIPLE_BROKER_TARGETS",),
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(identity_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            evidence=base_evidence,
        )

    selected = identity_matched_rows[0]
    target = _target_from_row(selected, expected_row)
    control_source = _control_source(row, selected)
    control_fp = broker_control_fingerprint_v3(control_source)
    viewport_fp = broker_viewport_fingerprint_v3(target.viewport)
    selected_evidence: dict[str, object] = {
        **base_evidence,
        "selected": target.as_dict(),
        "viewport_valid": _viewport_valid(target.viewport, expected_row),
        "controls_ready": _controls_ready(control_source),
        "pixel_fingerprint_present": bool(pixel_fp),
        "control_fingerprint_present": bool(control_fp),
        "broker_like_pixels": guard.broker_like_pixels,
    }

    if not target.window_handle and not target.target_id:
        return _lock_result(
            MISSING_TARGET_IDENTITY,
            "The selected broker target lacks both a window handle and a DevTools target id.",
            ("MISSING_WINDOW_HANDLE_OR_TARGET_ID",),
            selected_target=target,
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(identity_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            broker_control_fingerprint=control_fp,
            viewport_fingerprint=viewport_fp,
            evidence=selected_evidence,
        )

    if not _viewport_valid(target.viewport, expected_row):
        return _lock_result(
            VIEWPORT_MISMATCH,
            "The selected broker target viewport is missing, out of bounds, or different from the expected viewport.",
            ("VIEWPORT_MISMATCH",),
            selected_target=target,
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(identity_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            broker_control_fingerprint=control_fp,
            viewport_fingerprint=viewport_fp,
            evidence=selected_evidence,
        )

    source_pixels_valid = bool(guard.broker_like_pixels or chart_source_like)
    if image is not None and not source_pixels_valid:
        reason = (
            "The chart-study title or URL matched, but the captured pixels do not show a visible candle chart."
            if study_source_expected
            else "The Edge broker title or URL matched, but the captured pixels do not show broker controls."
        )
        reason_codes = (
            ("TITLE_MATCH_PIXEL_MISMATCH", "CHART_SOURCE_PIXELS_MISSING")
            if study_source_expected
            else ("TITLE_MATCH_PIXEL_MISMATCH", "BROKER_CONTROL_PIXELS_MISSING")
        )
        return _lock_result(
            TITLE_MATCH_PIXEL_MISMATCH,
            reason,
            reason_codes,
            selected_target=target,
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(identity_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            broker_control_fingerprint=control_fp,
            viewport_fingerprint=viewport_fp,
            evidence=selected_evidence,
        )

    expected_pixel_fp = _expected_pixel_fingerprint(expected_row)
    if expected_pixel_fp and pixel_fp and expected_pixel_fp != pixel_fp:
        selected_evidence["expected_broker_pixel_fingerprint"] = expected_pixel_fp
        return _lock_result(
            TITLE_MATCH_PIXEL_MISMATCH,
            "The selected broker target pixels changed from the expected broker pixel fingerprint.",
            ("BROKER_PIXEL_FINGERPRINT_MISMATCH",),
            selected_target=target,
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(identity_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            broker_control_fingerprint=control_fp,
            viewport_fingerprint=viewport_fp,
            evidence=selected_evidence,
        )

    expected_control_fp = _expected_control_fingerprint(expected_row)
    if expected_control_fp and control_fp and expected_control_fp != control_fp:
        selected_evidence["expected_broker_control_fingerprint"] = expected_control_fp
        return _lock_result(
            TITLE_MATCH_PIXEL_MISMATCH,
            "The selected broker controls changed from the expected broker control fingerprint.",
            ("BROKER_CONTROL_FINGERPRINT_MISMATCH",),
            selected_target=target,
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(identity_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            broker_control_fingerprint=control_fp,
            viewport_fingerprint=viewport_fp,
            evidence=selected_evidence,
        )

    has_fingerprint_evidence = bool(guard.broker_like_pixels or _controls_ready(control_source) or chart_source_like)
    if not has_fingerprint_evidence:
        return _lock_result(
            BROKER_NOT_FOUND,
            "Broker target text matched, but no broker pixel or control fingerprint evidence was available.",
            ("BROKER_FINGERPRINT_EVIDENCE_MISSING",),
            selected_target=target,
            candidate_count=len(candidate_rows),
            matching_candidate_count=len(identity_matched_rows),
            surface_guard=guard,
            broker_pixel_fingerprint=pixel_fp,
            broker_control_fingerprint=control_fp,
            viewport_fingerprint=viewport_fp,
            evidence=selected_evidence,
        )

    return _lock_result(
        VALID,
        (
            "Chart study source is locked by title/URL, target identity, viewport, and candle pixel evidence."
            if study_source_expected
            else "Edge broker source is locked by title/URL, target identity, viewport, and broker pixel/control evidence."
        ),
        ("CHART_STUDY_SOURCE_LOCKED",) if study_source_expected else ("BROKER_SOURCE_LOCKED",),
        selected_target=target,
        candidate_count=len(candidate_rows),
        matching_candidate_count=len(identity_matched_rows),
        surface_guard=guard,
        broker_pixel_fingerprint=pixel_fp,
        broker_control_fingerprint=control_fp,
        viewport_fingerprint=viewport_fp,
        evidence=selected_evidence,
    )


def build_broker_source_lock_v3(
    payload: Mapping[str, Any] | None = None,
    *,
    image: Image.Image | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    expected: Mapping[str, Any] | None = None,
) -> BrokerSourceLockV3:
    return evaluate_broker_source_lock_v3(payload, image=image, candidates=candidates, expected=expected)


__all__ = [
    "AMBIGUOUS_BROKER_TARGET",
    "BROKER_NOT_FOUND",
    "BROKER_SOURCE_LOCK_STATUSES",
    "BROKER_SOURCE_LOCK_V3_SCHEMA_VERSION",
    "BROKER_SURFACE",
    "BrokerSourceLockStatusV3",
    "BrokerSourceLockV3",
    "BrokerSourceTargetV3",
    "CHATGPT",
    "DASHBOARD_WINDOW_CAPTURE_GUARD_V3_SCHEMA_VERSION",
    "DashboardWindowCaptureGuardV3",
    "FINGERPRINT_MISMATCH",
    "INVALID_BROWSER",
    "MISSING_TARGET_IDENTITY",
    "NON_BROKER_CAPTURE",
    "PHOENIXGUARD_DASHBOARD",
    "TERMINAL",
    "TITLE_MATCH_PIXEL_MISMATCH",
    "UNKNOWN",
    "VALID",
    "VIEWPORT_MISMATCH",
    "VISUAL_STUDIO_CODE",
    "WINDOWS_DESKTOP_TASKBAR",
    "WRONG_SURFACE",
    "WRONG_SURFACE_CLASSES",
    "WrongSurfaceClassV3",
    "WrongSurfaceClassificationV3",
    "broker_control_fingerprint_v3",
    "broker_pixel_fingerprint_v3",
    "broker_viewport_fingerprint_v3",
    "build_broker_source_lock_v3",
    "classify_wrong_surface_v3",
    "evaluate_broker_source_lock_v3",
    "looks_like_pocket_option_broker_surface_v3",
]
