from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import stat as stat_module
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from PIL import Image, ImageDraw, ImageFont


SCHEMA_VERSION = "PG_ENTRY_ALLOWANCE_BURN_V1"
SOFT_BLOCKED_STUDY_REASONS = {
    "REASONING_WATCH",
    "REASONING_WAIT_FOR_PULLBACK",
    "REASONING_WAIT_FOR_RETEST",
    "REASONING_WAIT_FOR_REJECTION",
    "REASONING_WAIT_FOR_BREAK_CONFIRMATION",
}
HARD_BLOCKED_STUDY_TOKENS = (
    "STALE",
    "LATE",
    "TRAP",
    "BAD_ENTRY",
    "NO_PATH",
    "OPPOSING",
    "SUPPORT_LOCATION_GUARD",
    "RESISTANCE_LOCATION_GUARD",
    "BUY_LOW_SELL_HIGH",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_root() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if base:
        return Path(base) / "PhoenixGuard"
    return Path.cwd() / ".codex_runtime"


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(dict(payload), indent=2, ensure_ascii=True, default=str)
    last_error: OSError | None = None
    for attempt in range(12):
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            # Windows can briefly deny os.replace while dashboards or monitors
            # read status.json. A bounded retry keeps long burns alive.
            if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32}:
                time.sleep(min(0.5, 0.05 * (attempt + 1)))
                continue
            raise
    raise last_error or PermissionError(f"Unable to write {path}")


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=True, default=str, separators=(",", ":")) + "\n")


def mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def text(value: Any, default: str = "") -> str:
    raw = str(value if value is not None else default).strip()
    return raw or default


def side(value: Any, default: str = "") -> str:
    raw = text(value, default).upper()
    return raw if raw in {"BUY", "SELL"} else default


def number(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", [], {}):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def fetch_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        payload = json.loads(raw.decode("utf-8"))
        return {"ok": True, "url": url, "latency_ms": elapsed_ms, "json": payload if isinstance(payload, dict) else {}}
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {"ok": False, "url": url, "latency_ms": elapsed_ms, "json": {}, "error": f"{type(exc).__name__}: {exc}"}


def fetch_runtime_bundle(base_url: str, session_id: str, timeout: float) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Read live, council, and performance state without serial endpoint delay."""
    paths = {
        "live": f"/v1/mobile/live/state/v3/{session_id}?compact=1",
        "council": f"/v1/mobile/model-council/latest?session_id={session_id}",
        "perf": f"/v1/mobile/performance/trace/v3/{session_id}",
    }
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="pg-burn-fetch") as pool:
        futures = {name: pool.submit(fetch_json, base_url, path, timeout) for name, path in paths.items()}
        return futures["live"].result(), futures["council"].result(), futures["perf"].result()


def font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    candidates = (
        ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf")
        if bold
        else ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf")
    )
    for raw in candidates:
        path = Path(raw)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass
    return ImageFont.load_default()


def bbox_center(value: Any) -> tuple[float | None, float | None]:
    vals = sequence(value)
    if len(vals) < 4:
        return None, None
    try:
        x0, y0, x1, y1 = [float(item) for item in vals[:4]]
    except (TypeError, ValueError):
        return None, None
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def _point_from_row(row: Mapping[str, Any], width: int, height: int) -> tuple[int, int] | None:
    center_x, center_y = bbox_center(row.get("bbox") or row.get("box") or row.get("rect") or row.get("bounds"))
    x = number(row.get("center_x"), None)
    if x is None:
        x = number(row.get("line_x"), None)
    if x is None:
        x = number(row.get("x"), None)
    if x is None:
        x = center_x
    y = number(row.get("center_y"), None)
    if y is None:
        y = number(row.get("line_y"), None)
    if y is None:
        y = number(row.get("entry_y"), None)
    if y is None:
        y = number(row.get("current_y"), None)
    if y is None:
        y = number(row.get("y"), None)
    if y is None:
        y = center_y
    if x is None or y is None:
        return None
    return max(0, min(width - 1, int(round(x)))), max(0, min(height - 1, int(round(y))))


def _current_price_point_from_now_object(
    obj: Mapping[str, Any],
    width: int,
    height: int,
    entry_side: str,
) -> tuple[int, int] | None:
    bounds = sequence(obj.get("bbox") or obj.get("bounds") or obj.get("box") or obj.get("rect"))
    explicit_x = number(obj.get("current_x") or obj.get("latest_x") or obj.get("close_x") or obj.get("price_x"))
    explicit_y = number(obj.get("current_y") or obj.get("latest_y") or obj.get("close_y") or obj.get("price_y"))
    if explicit_x is not None and explicit_y is not None:
        return max(0, min(width - 1, int(round(explicit_x)))), max(0, min(height - 1, int(round(explicit_y))))
    if len(bounds) < 4:
        return _point_from_row(obj, width, height)
    try:
        _x0, y0, x1, y1 = [float(item) for item in bounds[:4]]
    except (TypeError, ValueError):
        return _point_from_row(obj, width, height)
    # The burn evidence must mark the live entry candle, not a historical zone.
    # Without an explicit close/current point, use the current candle's right edge
    # and the side-aware live-price edge of that current candle box.
    x = x1
    y = max(y0, y1) if entry_side == "SELL" else min(y0, y1)
    return max(0, min(width - 1, int(round(x)))), max(0, min(height - 1, int(round(y))))


def _current_candle_marker(live: Mapping[str, Any], image_size: tuple[int, int], entry_side: str) -> tuple[int, int, str] | None:
    width, height = image_size
    for row in sequence(live.get("overlay_objects")) + sequence(live.get("overlays")):
        obj = mapping(row)
        role = text(obj.get("role")).lower()
        obj_type = text(obj.get("type")).upper()
        label = text(obj.get("label") or obj.get("display_label") or obj.get("short_label")).upper()
        if role == "current_candle" or obj_type == "CURRENT_CANDLE" or label == "NOW":
            point = _current_price_point_from_now_object(obj, width, height, entry_side)
            if point:
                return point[0], point[1], "LATEST_CANDLE_NOW"
    tracking = mapping(live.get("tracking_summary"))
    candles = [mapping(item) for item in sequence(tracking.get("tracked_candles")) if mapping(item)]
    if candles:
        latest = max(candles, key=lambda item: number(item.get("center_x"), -1.0) or -1.0)
        point = _point_from_row(latest, width, height)
        if point:
            return point[0], point[1], "LATEST_TRACKED_CANDLE"
    return None


def marker_point(live: Mapping[str, Any], council: Mapping[str, Any], image_size: tuple[int, int], entry_side: str) -> tuple[int, int, str] | None:
    del council
    latest_candle = _current_candle_marker(live, image_size, entry_side)
    if latest_candle:
        return latest_candle
    return None


def focus_offset(live: Mapping[str, Any], window_size: tuple[int, int]) -> tuple[int, int]:
    tracking = mapping(live.get("tracking_summary"))
    focus = mapping(tracking.get("focus_region"))
    bbox = sequence(focus.get("pixel_bbox"))
    if len(bbox) >= 4:
        return int(round(number(bbox[0], 0.0) or 0.0)), int(round(number(bbox[1], 0.0) or 0.0))
    manual = mapping(live.get("manual_focus_region"))
    normalized = sequence(manual.get("normalized_bbox"))
    if len(normalized) >= 4:
        return int(round((number(normalized[0], 0.0) or 0.0) * window_size[0])), int(round((number(normalized[1], 0.0) or 0.0) * window_size[1]))
    return 0, 0


def annotate(
    image: Image.Image,
    entry_side: str,
    point: tuple[int, int],
    label: str,
    *,
    status_label: str = "ENTRY ALLOWED",
) -> Image.Image:
    img = image.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    width, height = img.size
    x, y = point
    color = (20, 220, 95) if entry_side == "BUY" else (255, 70, 70)
    outline = (0, 0, 0)
    arrow_len = max(56, min(120, int(height * 0.12)))
    if entry_side == "BUY":
        start = (x, min(height - 8, y + arrow_len))
        end = (x, max(8, y))
        head = [(x, max(8, y)), (max(0, x - 16), min(height - 1, y + 30)), (min(width - 1, x + 16), min(height - 1, y + 30))]
    else:
        start = (x, max(8, y - arrow_len))
        end = (x, min(height - 8, y))
        head = [(x, min(height - 8, y)), (max(0, x - 16), max(0, y - 30)), (min(width - 1, x + 16), max(0, y - 30))]
    for offset in (-2, -1, 0, 1, 2):
        draw.line([(start[0] + offset, start[1]), (end[0] + offset, end[1])], fill=outline, width=8)
    draw.line([start, end], fill=color, width=5)
    draw.polygon(head, fill=color, outline=outline)
    draw.ellipse([x - 16, y - 16, x + 16, y + 16], outline=outline, width=6)
    draw.ellipse([x - 16, y - 16, x + 16, y + 16], outline=color, width=3)
    label_text = f"{entry_side} {status_label[:32]} | {label[:40]}"
    label_font = font(18, bold=True)
    try:
        box = draw.textbbox((0, 0), label_text, font=label_font)
        tw = int(box[2] - box[0])
        th = int(box[3] - box[1])
    except Exception:
        tw = min(width - 20, len(label_text) * 10)
        th = 24
    tx = max(8, min(width - tw - 18, x - tw // 2))
    ty = max(8, min(height - th - 18, y - arrow_len - th - 20 if entry_side == "BUY" else y + 28))
    draw.rectangle([tx - 7, ty - 5, tx + tw + 7, ty + th + 7], fill=(0, 0, 0), outline=color, width=2)
    draw.text((tx, ty), label_text, fill=(255, 255, 255), font=label_font)
    return img


def save_jpeg(image: Image.Image, path: Path, quality: int = 82) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="JPEG", quality=max(40, min(95, int(quality))), optimize=False, progressive=False)


def local_artifact_path(value: Any) -> Path | None:
    raw = text(value)
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() and path.is_file() else None


def newest_local_artifact_path(values: Sequence[Any]) -> Path | None:
    candidates = [path for path in (local_artifact_path(value) for value in values) if path is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0.0)


def live_window_artifact_path(live: Mapping[str, Any]) -> Path | None:
    broker_surface = mapping(live.get("broker_surface"))
    broker_frame = mapping(broker_surface.get("frame"))
    return newest_local_artifact_path(
        [
            live.get("last_display_window_path"),
            live.get("last_window_path"),
            live.get("last_frame_path"),
            broker_frame.get("path"),
        ]
    )


def file_sha256(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def update_pixel_freeze_state(
    state: dict[str, Any],
    live: Mapping[str, Any],
    observed_at: float,
) -> dict[str, Any]:
    max_static_sec = float(os.getenv("PHOENIXGUARD_BURN_MAX_STATIC_PIXEL_SEC", "30") or "30")
    hard_static_sec = float(os.getenv("PHOENIXGUARD_BURN_HARD_STATIC_PIXEL_SEC", "180") or "180")
    hard_static_sec = max(max_static_sec, hard_static_sec)
    artifact_path = live_window_artifact_path(live)
    digest = file_sha256(artifact_path)
    frame_id = int(number(live.get("display_frame_id") or live.get("frame_id") or live.get("frame_index"), 0.0) or 0)
    capture_count = int(number(live.get("capture_count"), 0.0) or 0)
    artifact_sig = text(live.get("last_display_surface_signature") or live.get("last_window_surface_signature"))
    study_sig = text(live.get("last_study_surface_signature") or live.get("overlay_source_study_signature"))
    if not digest:
        state.clear()
        state.update(
            {
                "status": "UNKNOWN",
                "reason": "BROKER_ARTIFACT_HASH_MISSING",
                "path": str(artifact_path or ""),
                "max_static_sec": max_static_sec,
            }
        )
        return dict(state)
    previous_digest = text(state.get("digest"))
    previous_frame = int(number(state.get("last_frame_id"), frame_id) or frame_id)
    previous_capture = int(number(state.get("last_capture_count"), capture_count) or capture_count)
    if digest != previous_digest:
        state.clear()
        state.update(
            {
                "status": "CHANGED",
                "digest": digest,
                "path": str(artifact_path or ""),
                "first_seen_epoch": observed_at,
                "last_seen_epoch": observed_at,
                "first_frame_id": frame_id,
                "last_frame_id": frame_id,
                "first_capture_count": capture_count,
                "last_capture_count": capture_count,
                "same_hash_sec": 0.0,
                "frame_delta": 0,
                "capture_delta": 0,
                "max_static_sec": max_static_sec,
                "hard_static_sec": hard_static_sec,
                "refresh_recommended": False,
                "artifact_surface_signature": artifact_sig,
                "study_surface_signature": study_sig,
            }
        )
        return dict(state)
    first_seen = float(number(state.get("first_seen_epoch"), observed_at) or observed_at)
    first_frame = int(number(state.get("first_frame_id"), previous_frame) or previous_frame)
    first_capture = int(number(state.get("first_capture_count"), previous_capture) or previous_capture)
    same_hash_sec = max(0.0, observed_at - first_seen)
    frame_delta = max(0, frame_id - first_frame)
    capture_delta = max(0, capture_count - first_capture)
    state.update(
        {
            "status": "STATIC",
            "digest": digest,
            "path": str(artifact_path or ""),
            "last_seen_epoch": observed_at,
            "last_frame_id": frame_id,
            "last_capture_count": capture_count,
            "same_hash_sec": round(same_hash_sec, 3),
            "frame_delta": frame_delta,
            "capture_delta": capture_delta,
            "max_static_sec": max_static_sec,
            "hard_static_sec": hard_static_sec,
            "artifact_surface_signature": artifact_sig,
            "study_surface_signature": study_sig,
        }
    )
    refresh_recommended = same_hash_sec > max_static_sec and (frame_delta > 0 or capture_delta > 0)
    hard_stale = same_hash_sec > hard_static_sec and (frame_delta > 0 or capture_delta > 0)
    state["refresh_recommended"] = bool(refresh_recommended)
    if hard_stale:
        state["status"] = "FROZEN"
        state["reason"] = f"BROKER_PIXELS_FROZEN_{round(same_hash_sec, 1)}S_GT_{round(hard_static_sec, 1)}S"
    elif refresh_recommended:
        state["status"] = "STATIC_REFRESH"
        state["reason"] = f"BROKER_PIXELS_STATIC_{round(same_hash_sec, 1)}S_GT_{round(max_static_sec, 1)}S"
    else:
        state["status"] = "STATIC"
        state.pop("reason", None)
    return dict(state)


def apply_pixel_freeze_guard(sample: dict[str, Any], pixel_state: Mapping[str, Any]) -> dict[str, Any]:
    if text(pixel_state.get("status")).upper() != "FROZEN":
        sample["pixel_freeze"] = dict(pixel_state)
        sample["freshness"]["pixel_freeze"] = dict(pixel_state)
        return sample
    freshness = dict(mapping(sample.get("freshness")))
    reasons = [text(item) for item in sequence(freshness.get("reasons")) if text(item)]
    reason = text(pixel_state.get("reason"), "BROKER_PIXELS_FROZEN")
    if reason not in reasons:
        reasons.append(reason)
    freshness["fresh"] = False
    freshness["reasons"] = reasons
    freshness["pixel_freeze"] = dict(pixel_state)
    sample["freshness"] = freshness
    sample["pixel_freeze"] = dict(pixel_state)
    sample["entry"] = apply_freshness_guard(mapping(sample.get("entry")), freshness)
    return sample


def fetch_image(base_url: str, path: str, timeout: float) -> Image.Image | None:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read()
        return Image.open(BytesIO(raw)).convert("RGB")
    except Exception:
        return None


def artifact_frame_from_path(value: Any) -> int | None:
    raw = text(value)
    if not raw:
        return None
    match = re.search(
        r"(?:^|[\\/])(\d{1,12})_[^\\/]+_(?:window|chart|overlay|full_overlay)(?:\.|$)",
        raw,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def current_artifact_frame(live: Mapping[str, Any], sample: Mapping[str, Any]) -> int:
    for value in (
        live.get("frame_index"),
        live.get("frame_id"),
        mapping(sample.get("frames")).get("capture_count"),
        mapping(sample.get("frames")).get("display_frame_id"),
    ):
        result = number(value)
        if result is not None and result > 0:
            return int(result)
    return 0


def artifact_frame_age(
    path: Path | None,
    live: Mapping[str, Any],
    sample: Mapping[str, Any],
    *,
    frame_keys: Sequence[str] = (),
) -> dict[str, Any]:
    artifact_frame = artifact_frame_from_path(path) if path is not None else None
    if artifact_frame is None:
        for key in frame_keys:
            fallback_frame = number(live.get(key))
            if fallback_frame is not None and fallback_frame > 0:
                artifact_frame = int(fallback_frame)
                break
    current_frame = current_artifact_frame(live, sample)
    if artifact_frame is None or current_frame <= 0:
        return {
            "current_frame": current_frame,
            "artifact_frame": artifact_frame,
            "age_frames": None,
            "status": "UNKNOWN",
        }
    age = max(0, current_frame - artifact_frame)
    max_age = int(float(os.getenv("PHOENIXGUARD_ENTRY_EVIDENCE_OVERLAY_MAX_FRAME_AGE", "3") or "3"))
    return {
        "current_frame": current_frame,
        "artifact_frame": artifact_frame,
        "age_frames": age,
        "max_age_frames": max_age,
        "status": "PASS" if age <= max_age else "STALE",
    }


def chart_crop_box(live: Mapping[str, Any], window_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = window_size
    tracking = mapping(live.get("tracking_summary"))
    focus = mapping(tracking.get("focus_region"))
    bbox = sequence(focus.get("pixel_bbox"))
    if len(bbox) >= 4:
        x0 = max(0, min(width - 1, int(round(number(bbox[0], 0.0) or 0.0))))
        y0 = max(0, min(height - 1, int(round(number(bbox[1], 0.0) or 0.0))))
        x1 = max(x0 + 1, min(width, int(round(number(bbox[2], width) or width))))
        y1 = max(y0 + 1, min(height, int(round(number(bbox[3], height) or height))))
        return x0, y0, x1, y1
    manual = mapping(live.get("manual_focus_region"))
    normalized = sequence(manual.get("normalized_bbox"))
    if len(normalized) >= 4:
        x0 = max(0, min(width - 1, int(round((number(normalized[0], 0.0) or 0.0) * width))))
        y0 = max(0, min(height - 1, int(round((number(normalized[1], 0.0) or 0.0) * height))))
        x1 = max(x0 + 1, min(width, int(round((number(normalized[2], 1.0) or 1.0) * width))))
        y1 = max(y0 + 1, min(height, int(round((number(normalized[3], 1.0) or 1.0) * height))))
        return x0, y0, x1, y1
    return 0, 0, width, height


def overlay_color(row: Mapping[str, Any]) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    obj_type = text(row.get("type") or row.get("label") or row.get("role")).upper()
    family = text(mapping(row.get("style")).get("semantic_family")).lower()
    side_value = side(row.get("side"))
    key = f"{obj_type} {family} {side_value}".upper()
    if any(token in key for token in ("SUPPLY", "RESISTANCE", "SELL")):
        return (255, 73, 64, 235), (255, 73, 64, 38)
    if any(token in key for token in ("DEMAND", "SUPPORT", "BUY")):
        return (30, 220, 115, 235), (30, 220, 115, 38)
    if "TARGET" in key or "TRIGGER" in key:
        return (255, 211, 77, 240), (255, 211, 77, 48)
    if "TRENDLINE" in key:
        return (76, 128, 255, 240), (76, 128, 255, 36)
    if "CURRENT_CANDLE" in key or text(row.get("label")).upper() == "NOW":
        return (255, 255, 255, 245), (255, 255, 255, 36)
    return (185, 165, 255, 220), (185, 165, 255, 34)


def _clamped_point(value: Sequence[Any], width: int, height: int) -> tuple[int, int] | None:
    if len(value) < 2:
        return None
    x = number(value[0])
    y = number(value[1])
    if x is None or y is None:
        return None
    return max(0, min(width - 1, int(round(x)))), max(0, min(height - 1, int(round(y))))


def render_live_json_overlay(live: Mapping[str, Any], window_image: Image.Image) -> Image.Image | None:
    objects = [mapping(item) for item in sequence(live.get("overlay_objects")) + sequence(live.get("overlays"))]
    objects = [item for item in objects if item]
    if not objects:
        return None
    crop = chart_crop_box(live, window_image.size)
    base = window_image.crop(crop).convert("RGBA")
    width, height = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    label_font = font(15, bold=True)
    ordered = sorted(objects, key=lambda item: number(item.get("visual_weight"), 0.5) or 0.5)
    for row in ordered[:120]:
        obj_type = text(row.get("type") or row.get("label") or row.get("role")).upper()
        if obj_type in {"REPLAY_ENTRY", "REPLAY_EXIT"}:
            continue
        stroke, fill = overlay_color(row)
        points_raw = sequence(row.get("points") or row.get("line_points"))
        points = [_clamped_point(sequence(point), width, height) for point in points_raw]
        points = [point for point in points if point is not None]
        if len(points) >= 2:
            draw.line(points, fill=stroke, width=5 if "TRENDLINE" in obj_type else 3, joint="curve")
        bbox = sequence(row.get("bbox") or row.get("bounds") or row.get("box") or row.get("rect"))
        if len(bbox) >= 4:
            x0 = max(0, min(width - 1, int(round(number(bbox[0], 0.0) or 0.0))))
            y0 = max(0, min(height - 1, int(round(number(bbox[1], 0.0) or 0.0))))
            x1 = max(x0 + 1, min(width, int(round(number(bbox[2], width) or width))))
            y1 = max(y0 + 1, min(height, int(round(number(bbox[3], height) or height))))
            line_width = 5 if obj_type == "CURRENT_CANDLE" else 3 if any(token in obj_type for token in ("TRENDLINE", "SUPPLY", "DEMAND", "TARGET", "TRIGGER", "SNIPER")) else 2
            draw.rectangle([x0, y0, x1, y1], fill=fill, outline=stroke, width=line_width)
        label = text(row.get("short_label") or row.get("display_label") or row.get("label"))
        label_hidden = bool(row.get("label_hidden")) or row.get("label_visible") is False
        if label and not label_hidden:
            label_bounds = mapping(row.get("label_bounds"))
            label_box = sequence(label_bounds.get("bbox"))
            if len(label_box) >= 2:
                tx = max(0, min(width - 40, int(round(number(label_box[0], 0.0) or 0.0))))
                ty = max(0, min(height - 20, int(round(number(label_box[1], 0.0) or 0.0))))
            elif len(bbox) >= 2:
                tx = max(0, min(width - 40, int(round(number(bbox[0], 0.0) or 0.0))))
                ty = max(0, min(height - 20, int(round(number(bbox[1], 0.0) or 0.0))))
            else:
                continue
            try:
                text_box = draw.textbbox((tx, ty), label[:34], font=label_font)
                draw.rectangle([text_box[0] - 4, text_box[1] - 3, text_box[2] + 4, text_box[3] + 3], fill=(0, 0, 0, 170), outline=stroke, width=1)
            except Exception:
                pass
            draw.text((tx, ty), label[:34], fill=(255, 255, 245, 255), font=label_font)
    return Image.alpha_composite(base, overlay).convert("RGB")


def session_artifact_payloads(session_id: str) -> list[dict[str, Any]]:
    session_dir = local_root() / "codex_runtime" / "data_live" / "mobile_api" / "window_tracker" / "sessions" / session_id
    rows: list[dict[str, Any]] = []
    for name in ("session.json", "display_state.json"):
        try:
            raw = json.loads((session_dir / name).read_text(encoding="utf-8"))
            rows.append(mapping(raw))
        except Exception:
            rows.append({})
    return rows


def capture_entry_evidence(
    out_dir: Path,
    sample: Mapping[str, Any],
    live: Mapping[str, Any],
    council: Mapping[str, Any],
    session_id: str,
    base_url: str,
    timeout_sec: float,
) -> dict[str, Any]:
    entry = mapping(sample.get("entry"))
    entry_side = side(entry.get("side"))
    if entry_side not in {"BUY", "SELL"}:
        return {}
    session_payloads = session_artifact_payloads(session_id)
    artifact_sources: list[Mapping[str, Any]] = [live, *session_payloads]
    overlay_path = newest_local_artifact_path(
        [
            row.get("last_overlay_path") or row.get("last_full_overlay_path") or row.get("last_chart_path")
            for row in artifact_sources
        ]
        + [row.get("last_full_overlay_path") for row in artifact_sources]
        + [row.get("last_chart_path") for row in artifact_sources]
    )
    window_path = newest_local_artifact_path(
        [
            row.get("last_display_window_path") or row.get("last_window_path") or row.get("last_frame_path")
            for row in artifact_sources
        ]
        + [row.get("last_window_path") for row in artifact_sources]
        + [row.get("last_frame_path") for row in artifact_sources]
    )
    overlay_image: Image.Image | None = None
    window_image: Image.Image | None = None
    overlay_source_mode = "none"
    window_source_mode = "none"
    if overlay_path is not None:
        try:
            overlay_image = Image.open(overlay_path).convert("RGB")
            overlay_source_mode = "local_artifact"
        except Exception:
            overlay_image = None
    if window_path is not None:
        try:
            window_image = Image.open(window_path).convert("RGB")
            window_source_mode = "local_artifact"
        except Exception:
            window_image = None
    if window_image is None:
        window_image = fetch_image(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window", timeout_sec)
        if window_image is not None:
            window_source_mode = "http_latest_window"
    window_freshness = artifact_frame_age(window_path, live, sample, frame_keys=("frame_index", "frame_id"))
    if window_image is not None and window_freshness.get("status") == "STALE":
        fetched_window = fetch_image(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window", timeout_sec)
        if fetched_window is not None:
            window_image = fetched_window
            window_source_mode = "http_latest_window_after_stale_local"
            window_path = None
            window_freshness: dict[str, Any] = {
                "current_frame": current_artifact_frame(live, sample),
                "artifact_frame": None,
                "age_frames": None,
                "status": "HTTP_LATEST_UNNUMBERED",
            }
    overlay_freshness = artifact_frame_age(
        overlay_path,
        live,
        sample,
        frame_keys=("overlay_frame_id", "chart_frame_id", "frame_index", "frame_id"),
    )
    if window_image is not None and (overlay_image is None or overlay_freshness.get("status") == "STALE"):
        rendered = render_live_json_overlay(live, window_image)
        if rendered is not None:
            overlay_image = rendered
            overlay_source_mode = "rendered_live_json_on_current_window_crop"
        elif overlay_freshness.get("status") == "STALE":
            overlay_image = None
            overlay_source_mode = "stale_overlay_rejected"
    if overlay_image is None and overlay_freshness.get("status") != "STALE":
        overlay_image = fetch_image(base_url, f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-overlay", timeout_sec)
        if overlay_image is not None:
            overlay_source_mode = "http_latest_overlay"
    overlay_source_ref = str(overlay_path or "http_latest_overlay")
    if overlay_source_mode == "rendered_live_json_on_current_window_crop":
        overlay_source_ref = f"rendered_live_json_on_current_window_crop:{window_path or 'http_latest_window'}"
    window_source_ref = str(window_path or "http_latest_window")
    if overlay_image is None or window_image is None:
        return {
            "seq": int(sample.get("seq", 0) or 0),
            "frame": mapping(sample.get("frames")).get("display_frame_id"),
            "side": entry_side,
            "captured_at_utc": sample.get("captured_at_utc"),
            "error": "missing_local_artifact_path",
            "live_paths": {
                "last_overlay_path": text(live.get("last_overlay_path")),
                "last_full_overlay_path": text(live.get("last_full_overlay_path")),
                "last_chart_path": text(live.get("last_chart_path")),
                "last_window_path": text(live.get("last_window_path")),
                "last_display_window_path": text(live.get("last_display_window_path")),
                "last_frame_path": text(live.get("last_frame_path")),
            },
            "overlay_source_mode": overlay_source_mode,
            "window_source_mode": window_source_mode,
            "overlay_freshness": overlay_freshness,
            "window_freshness": window_freshness,
        }
    evidence_dir = out_dir / "entry_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    chart_point = marker_point(live, council, overlay_image.size, entry_side)
    entry_allowed = bool(entry.get("allowed"))
    evidence_status_label = "ENTRY ALLOWED" if entry_allowed else "ENTER NOW BLOCKED"
    if chart_point is None:
        seq = int(sample.get("seq", 0) or 0)
        frame = int(mapping(sample.get("frames")).get("display_frame_id") or 0)
        stamp = int(float(sample.get("captured_epoch") or time.time()) * 1000.0)
        evidence_kind = "entry_marker_unresolved" if entry_allowed else "blocked_enter_now_marker_unresolved"
        stem = f"{seq:05d}_{frame:06d}_{stamp}_{entry_side.lower()}_{evidence_kind}"
        failure_overlay = evidence_dir / f"{stem}_overlay.jpg"
        failure_broker = evidence_dir / f"{stem}_broker.jpg"
        failure_json = evidence_dir / f"{stem}.json"
        failure_label = "ENTRY MARKER UNRESOLVED | NO FALLBACK"
        warning_point = (max(24, overlay_image.size[0] - 240), max(24, overlay_image.size[1] // 2))
        broker_warning_point = (max(24, window_image.size[0] - 240), max(24, window_image.size[1] // 2))
        save_jpeg(annotate(overlay_image, entry_side, warning_point, failure_label, status_label=evidence_status_label), failure_overlay)
        save_jpeg(annotate(window_image, entry_side, broker_warning_point, failure_label, status_label=evidence_status_label), failure_broker)
        meta: dict[str, Any] = {
            "schema_version": "PG_ENTRY_ALLOWANCE_EVIDENCE_V1",
            "seq": seq,
            "frame": frame,
            "side": entry_side,
            "captured_at_utc": sample.get("captured_at_utc"),
            "captured_epoch": sample.get("captured_epoch"),
            "entry": entry,
            "error": "ENTRY_MARKER_UNRESOLVED",
            "marker_policy": "NO_FALLBACK_ONLY_LATEST_CANDLE_NOW",
            "rejected_fallbacks": ["SUPPORT", "RESISTANCE", "SUPPLY", "DEMAND", "ENTRY_ZONE", "CURRENT_BOX", "PRICE_PROXY"],
            "source_overlay_path": overlay_source_ref,
            "source_window_path": window_source_ref,
            "source_overlay_artifact_path": str(overlay_path or ""),
            "overlay_source_mode": overlay_source_mode,
            "window_source_mode": window_source_mode,
            "overlay_freshness": overlay_freshness,
            "window_freshness": window_freshness,
            "overlay_evidence_path": str(failure_overlay),
            "broker_evidence_path": str(failure_broker),
        }
        write_json(failure_json, meta)
        return meta
    off_x, off_y = focus_offset(live, window_image.size)
    window_point = (
        max(0, min(window_image.size[0] - 1, off_x + chart_point[0])),
        max(0, min(window_image.size[1] - 1, off_y + chart_point[1])),
    )
    seq = int(sample.get("seq", 0) or 0)
    frame = int(mapping(sample.get("frames")).get("display_frame_id") or 0)
    stamp = int(float(sample.get("captured_epoch") or time.time()) * 1000.0)
    evidence_kind = "entry" if entry_allowed else "blocked_enter_now"
    stem = f"{seq:05d}_{frame:06d}_{stamp}_{entry_side.lower()}_{evidence_kind}"
    overlay_out = evidence_dir / f"{stem}_overlay.jpg"
    broker_out = evidence_dir / f"{stem}_broker.jpg"
    meta_out = evidence_dir / f"{stem}.json"
    label = f"seq {seq} frame {frame} {chart_point[2]}"
    save_jpeg(annotate(overlay_image, entry_side, (chart_point[0], chart_point[1]), label, status_label=evidence_status_label), overlay_out)
    save_jpeg(annotate(window_image, entry_side, window_point, label, status_label=evidence_status_label), broker_out)
    meta: dict[str, Any] = {
        "schema_version": "PG_ENTRY_ALLOWANCE_EVIDENCE_V1",
        "seq": seq,
        "frame": frame,
        "side": entry_side,
        "captured_at_utc": sample.get("captured_at_utc"),
        "captured_epoch": sample.get("captured_epoch"),
        "entry": entry,
        "chart_point": {"x": chart_point[0], "y": chart_point[1], "source": chart_point[2]},
        "marker_source": chart_point[2],
        "window_point": {"x": window_point[0], "y": window_point[1]},
        "source_overlay_path": overlay_source_ref,
        "source_window_path": window_source_ref,
        "source_overlay_artifact_path": str(overlay_path or ""),
        "overlay_source_mode": overlay_source_mode,
        "window_source_mode": window_source_mode,
        "overlay_freshness": overlay_freshness,
        "window_freshness": window_freshness,
        "overlay_evidence_path": str(overlay_out),
        "broker_evidence_path": str(broker_out),
    }
    write_json(meta_out, meta)
    return meta


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path).lower()


def protected_session_artifact_paths(session_dir: Path) -> set[str]:
    protected: set[str] = set()
    for name in ("session.json", "display_state.json"):
        payload: dict[str, Any] = {}
        try:
            raw = json.loads((session_dir / name).read_text(encoding="utf-8"))
            payload = mapping(raw)
        except Exception:
            payload = {}
        for key in (
            "last_window_path",
            "last_frame_path",
            "last_display_window_path",
            "last_chart_path",
            "last_overlay_path",
            "last_full_overlay_path",
            "last_decision_path",
        ):
            raw_path = text(payload.get(key))
            if raw_path:
                protected.add(_path_key(Path(raw_path)))
    return protected


def prune_path_budget(
    path: Path,
    *,
    max_mb: float,
    max_files: int,
    max_age_sec: float,
    pattern: str = "*",
    protected_paths: set[str] | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "removed": 0, "removed_mb": 0.0, "scanned_files": 0, "scan_errors": 0}
    protected = set(protected_paths or set())
    records: list[tuple[Path, float, int]] = []
    scan_errors = 0
    try:
        iterator = path.iterdir()
    except OSError:
        return {"path": str(path), "removed": 0, "removed_mb": 0.0, "scan_errors": 1}
    for item in iterator:
        if pattern != "*" and not fnmatch.fnmatch(item.name, pattern):
            continue
        try:
            stat = item.stat()
        except OSError:
            scan_errors += 1
            continue
        if not stat_module.S_ISREG(stat.st_mode):
            continue
        records.append((item, float(stat.st_mtime), int(stat.st_size)))
    if not records:
        return {"path": str(path), "removed": 0, "removed_mb": 0.0, "scan_errors": scan_errors}
    size_by_path = {item: size for item, _mtime, size in records}
    mtime_by_path = {item: mtime for item, mtime, _size in records}
    now = time.time()
    ordered = [item for item, _mtime, _size in sorted(records, key=lambda row: row[1], reverse=True)]
    remove: set[Path] = set()
    for index, item in enumerate(ordered):
        age = now - mtime_by_path.get(item, now)
        if _path_key(item) in protected:
            continue
        if index >= max_files or age > max_age_sec:
            remove.add(item)
    kept = [item for item in ordered if item not in remove]
    total = sum(size_by_path.get(item, 0) for item in kept)
    max_bytes = int(max_mb * 1024.0 * 1024.0)
    for item in sorted(kept, key=lambda p: mtime_by_path.get(p, 0.0)):
        if total <= max_bytes:
            break
        if _path_key(item) in protected:
            continue
        total -= size_by_path.get(item, 0)
        remove.add(item)
    removed = 0
    removed_bytes = 0
    for item in remove:
        removed_bytes += size_by_path.get(item, 0)
        try:
            item.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return {
        "path": str(path),
        "removed": removed,
        "removed_mb": round(removed_bytes / 1024.0 / 1024.0, 3),
        "scanned_files": len(records),
        "scan_errors": scan_errors,
    }


def protected_allowed_entry_evidence_paths(evidence_dir: Path) -> set[str]:
    """Protect screenshots/metadata for actual allowed entries during burn pruning."""
    if not evidence_dir.exists():
        return set()
    protected: set[str] = set()
    try:
        iterator = evidence_dir.iterdir()
    except OSError:
        return protected
    for item in iterator:
        try:
            item_stat = item.stat()
        except OSError:
            continue
        if not stat_module.S_ISREG(item_stat.st_mode):
            continue
        name = item.name.lower()
        if "_entry" in name and "blocked_enter_now" not in name:
            protected.add(_path_key(item))
    return protected


def storage_guard(out_dir: Path, session_id: str) -> dict[str, Any]:
    root = local_root()
    repo_runtime = Path.cwd() / ".codex_runtime"
    session_dir = root / "codex_runtime" / "data_live" / "mobile_api" / "window_tracker" / "sessions" / session_id
    protected = protected_session_artifact_paths(session_dir)
    entry_evidence_dir = out_dir / "entry_evidence"
    protected_entry_evidence = protected_allowed_entry_evidence_paths(entry_evidence_dir)
    results: dict[str, Any] = {
        "at_utc": utc_now(),
        "overlay_geometry_dumps": prune_path_budget(
            repo_runtime / "overlay_geometry_dumps",
            max_mb=48.0,
            max_files=80,
            max_age_sec=7200.0,
            pattern="overlay_geometry_*.json",
        ),
        "entry_evidence": prune_path_budget(
            entry_evidence_dir,
            max_mb=float(os.getenv("PHOENIXGUARD_BURN_ENTRY_EVIDENCE_MAX_MB", "768") or "768"),
            max_files=int(float(os.getenv("PHOENIXGUARD_BURN_ENTRY_EVIDENCE_MAX_FILES", "1200") or "1200")),
            max_age_sec=float(os.getenv("PHOENIXGUARD_BURN_ENTRY_EVIDENCE_MAX_AGE_SEC", "7200") or "7200"),
            protected_paths=protected_entry_evidence,
        ),
        "raw_snapshots": prune_path_budget(
            out_dir / "raw",
            max_mb=float(os.getenv("PHOENIXGUARD_BURN_RAW_MAX_MB", "512") or "512"),
            max_files=int(float(os.getenv("PHOENIXGUARD_BURN_RAW_MAX_FILES", "1200") or "1200")),
            max_age_sec=float(os.getenv("PHOENIXGUARD_BURN_RAW_MAX_AGE_SEC", "18000") or "18000"),
            pattern="*.json",
        ),
    }
    results["entry_evidence"]["protected_allowed_entry_files"] = len(protected_entry_evidence)
    if os.getenv("PHOENIXGUARD_BURN_PRUNE_SESSION_ARTIFACTS", "1") != "0":
        results["session_entry_evidence"] = prune_path_budget(
            session_dir / "entry_evidence",
            max_mb=float(os.getenv("PHOENIXGUARD_ENTRY_EVIDENCE_MAX_MB", "512") or "512"),
            max_files=int(float(os.getenv("PHOENIXGUARD_ENTRY_EVIDENCE_MAX_FILES", "360") or "360")),
            max_age_sec=float(os.getenv("PHOENIXGUARD_ENTRY_EVIDENCE_MAX_AGE_SEC", "7200") or "7200"),
        )
        session_artifacts = session_dir / "artifacts"
        results["session_artifacts"] = prune_path_budget(
            session_artifacts,
            max_mb=float(os.getenv("PHOENIXGUARD_BURN_SESSION_ARTIFACT_MAX_MB", "256") or "256"),
            max_files=int(float(os.getenv("PHOENIXGUARD_BURN_SESSION_ARTIFACT_MAX_FILES", "360") or "360")),
            max_age_sec=float(os.getenv("PHOENIXGUARD_BURN_SESSION_ARTIFACT_MAX_AGE_SEC", "7200") or "7200"),
            protected_paths=protected,
        )
        results["session_decisions"] = prune_path_budget(
            session_artifacts,
            max_mb=48.0,
            max_files=12,
            max_age_sec=7200.0,
            pattern="*_decision.json",
            protected_paths=protected,
        )
    else:
        results["session_entry_evidence"] = {"path": str(session_dir / "entry_evidence"), "status": "disabled_by_env"}
        results["session_artifacts"] = {"path": str(session_dir / "artifacts"), "status": "disabled_by_env"}
        results["session_decisions"] = {"path": str(session_dir / "artifacts"), "status": "disabled_by_env"}
    return results


def artifact_health(session_id: str) -> dict[str, Any]:
    session_dir = local_root() / "codex_runtime" / "data_live" / "mobile_api" / "window_tracker" / "sessions" / session_id
    result: dict[str, Any] = {"session_dir": str(session_dir), "artifact_count": 0, "artifact_mb": 0.0, "counts": {}, "scan_errors": 0}
    if not session_dir.exists():
        return result
    counts: Counter[str] = Counter()
    total = 0
    scan_errors = 0
    for root, _dirs, files in os.walk(session_dir):
        for name in files:
            path = Path(root) / name
            try:
                item_stat = path.stat()
            except OSError:
                scan_errors += 1
                continue
            if not stat_module.S_ISREG(item_stat.st_mode):
                continue
            counts[path.suffix.lower() or "(none)"] += 1
            total += item_stat.st_size
    result["artifact_count"] = sum(counts.values())
    result["artifact_mb"] = round(total / 1024.0 / 1024.0, 3)
    result["counts"] = dict(counts)
    result["scan_errors"] = scan_errors
    return result


def disk_health() -> dict[str, Any]:
    usage = shutil.disk_usage(Path.cwd())
    return {
        "free_gb": round(usage.free / 1024.0 / 1024.0 / 1024.0, 3),
        "used_gb": round(usage.used / 1024.0 / 1024.0 / 1024.0, 3),
        "total_gb": round(usage.total / 1024.0 / 1024.0 / 1024.0, 3),
    }


def directory_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            child = Path(root) / name
            try:
                item_stat = child.stat()
            except OSError:
                continue
            if stat_module.S_ISREG(item_stat.st_mode):
                total += int(item_stat.st_size)
    return total


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def prune_hardening_studies(active_out_dir: Path) -> dict[str, Any]:
    root = local_root() / "hardening_studies"
    result: dict[str, Any] = {
        "path": str(root),
        "removed": 0,
        "removed_mb": 0.0,
        "kept": 0,
        "total_mb_after": 0.0,
    }
    if not root.exists():
        return result
    max_mb = float(os.getenv("PHOENIXGUARD_HARDENING_STUDIES_MAX_MB", "2048") or "2048")
    max_dirs = int(float(os.getenv("PHOENIXGUARD_HARDENING_STUDIES_MAX_DIRS", "6") or "6"))
    max_age_sec = float(os.getenv("PHOENIXGUARD_HARDENING_STUDIES_MAX_AGE_SEC", "172800") or "172800")
    active = active_out_dir.resolve()
    dirs = [path for path in root.iterdir() if path.is_dir()]
    now = time.time()
    ordered = sorted(dirs, key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    remove: set[Path] = set()
    for index, path in enumerate(ordered):
        if path.resolve() == active:
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = 0.0
        if index >= max_dirs or age > max_age_sec:
            remove.add(path)
    kept = [path for path in ordered if path not in remove]
    total = sum(directory_size_bytes(path) for path in kept)
    max_bytes = int(max_mb * 1024.0 * 1024.0)
    for path in sorted(kept, key=lambda p: p.stat().st_mtime if p.exists() else 0.0):
        if total <= max_bytes:
            break
        if path.resolve() == active:
            continue
        total -= directory_size_bytes(path)
        remove.add(path)
    removed_bytes = 0
    for path in remove:
        if path.resolve() == active or not is_relative_to(path, root):
            continue
        try:
            size = directory_size_bytes(path)
            shutil.rmtree(path)
            result["removed"] = int(result["removed"]) + 1
            removed_bytes += size
        except OSError:
            pass
    result["removed_mb"] = round(removed_bytes / 1024.0 / 1024.0, 3)
    result["kept"] = len([path for path in root.iterdir() if path.is_dir()]) if root.exists() else 0
    result["total_mb_after"] = round(directory_size_bytes(root) / 1024.0 / 1024.0, 3)
    return result


def clear_existing_hardening_studies() -> dict[str, Any]:
    root = local_root() / "hardening_studies"
    result: dict[str, Any] = {
        "path": str(root),
        "removed": 0,
        "removed_mb": 0.0,
        "status": "not_present",
    }
    if not root.exists():
        return result
    removed_bytes = 0
    for path in list(root.iterdir()):
        if not path.is_dir() or not is_relative_to(path, root):
            continue
        try:
            size = directory_size_bytes(path)
            shutil.rmtree(path)
            removed_bytes += size
            result["removed"] = int(result["removed"]) + 1
        except OSError as exc:
            result.setdefault("errors", []).append({"path": str(path), "error": str(exc)})
    result["removed_mb"] = round(removed_bytes / 1024.0 / 1024.0, 3)
    result["status"] = "cleared"
    return result


def entry_state(live: Mapping[str, Any], council: Mapping[str, Any]) -> dict[str, Any]:
    promotion = mapping(council.get("promotion_trace"))
    timing = mapping(promotion.get("timing_decision"))
    lane = mapping(promotion.get("execution_lane"))
    wave_context = mapping(promotion.get("wave_context") or lane.get("wave_context"))
    packet_present = bool(council.get("execution_packet_present") or council.get("execution_packet") or council.get("model_council_packet"))
    entry_now = bool(timing.get("entry_now_allowed"))
    lane_name = text(lane.get("name") or promotion.get("selected_lane") or council.get("selected_execution_lane")).upper()
    legacy_hf_lane_rejected = lane_name == "HIGH_FREQUENCY_TWO_CANDLE"
    lane_accepted = bool(lane.get("accepted") or promotion.get("lane_accepted")) and not legacy_hf_lane_rejected
    candidate = side(promotion.get("candidate_side") or timing.get("direction_side") or promotion.get("final_side"))
    if candidate not in {"BUY", "SELL"}:
        cycle = mapping(lane.get("high_frequency_candle_cycle") or promotion.get("high_frequency_candle_cycle"))
        next1 = mapping(cycle.get("next_candle_forecast"))
        candidate = side(next1.get("direction"))
    execution_authorized = bool(lane_accepted and packet_present)
    allowed = bool(candidate in {"BUY", "SELL"} and entry_now and lane_accepted and packet_present)
    return {
        "allowed": allowed,
        "side": candidate,
        "entry_now_allowed": entry_now,
        "lane_accepted": lane_accepted,
        "lane_name": lane_name,
        "legacy_hf_lane_rejected": legacy_hf_lane_rejected,
        "packet_present": packet_present,
        "execution_authorized": execution_authorized,
        "allowance_mode": "timing_entry_now",
        "timing_mode": text(timing.get("timing_mode") or mapping(timing.get("entry_timing")).get("mode")),
        "blocked_by": text(promotion.get("blocked_by") or promotion.get("denied_at")),
        "candidate_id": text(promotion.get("candidate_id")),
        "packet_id": text(promotion.get("packet_id") or council.get("execution_packet_id")),
        "final_score": number(promotion.get("final_score") or promotion.get("final_execution_score")),
        "threshold": number(promotion.get("threshold") or promotion.get("execution_threshold")),
        "next_required": text(promotion.get("next_required") or lane.get("reason")),
        "reasoning_state": text(promotion.get("reasoning_state") or mapping(council.get("final_reasoning_decision")).get("decision")).upper(),
        "reasoning_execution_blocked": bool(promotion.get("reasoning_execution_blocked")),
        "hard_bad_entry_class_active": bool(promotion.get("hard_bad_entry_class_active")),
        "reasoning_bad_entry_class": text(promotion.get("reasoning_bad_entry_class")).upper(),
        "market_bad_entry_class": text(promotion.get("market_bad_entry_class")).upper(),
        "opposing_force_ok": bool(promotion.get("opposing_force_ok")),
        "wave_phase": text(wave_context.get("phase")).upper(),
        "wave_entry_ok": bool(wave_context.get("wave_entry_ok")),
        "directional_location_ok": wave_context.get("directional_location_ok"),
        "directional_location_chase_risk": bool(wave_context.get("directional_location_chase_risk")),
        "sell_low_history_risk": bool(wave_context.get("sell_low_history_risk")),
        "buy_high_history_risk": bool(wave_context.get("buy_high_history_risk")),
        "entry_area_behind_price": bool(wave_context.get("entry_area_behind_price")),
        "history_area_label": text(wave_context.get("history_area_label")).upper(),
        "history_area_risk": number(wave_context.get("history_area_risk")),
    }


def runtime_freshness_state(
    live_resp: Mapping[str, Any],
    council_resp: Mapping[str, Any],
    perf_resp: Mapping[str, Any],
    live: Mapping[str, Any],
    perf: Mapping[str, Any],
) -> dict[str, Any]:
    max_frame_age_ms = float(os.getenv("PHOENIXGUARD_BURN_MAX_FRAME_AGE_MS", "2500") or "2500")
    max_capture_age_sec = float(os.getenv("PHOENIXGUARD_BURN_MAX_CAPTURE_AGE_SEC", "4") or "4")
    reject_published_age_warning = os.getenv("PHOENIXGUARD_BURN_REJECT_PUBLISHED_AGE_WARNING", "1") != "0"
    reject_capture_age_warning = os.getenv("PHOENIXGUARD_BURN_REJECT_CAPTURE_AGE_WARNING", "1") != "0"
    timing = mapping(perf.get("timing_trace"))
    visual = mapping(live.get("visual_health_v3") or perf.get("visual_health"))
    stale_status = text(
        timing.get("stale_status")
        or live.get("stale_status")
        or visual.get("stale_status")
        or visual.get("status")
    ).upper()
    frame_age_ms = number(timing.get("frame_age_ms"))
    published_epoch_ms = number(
        timing.get("display_published_epoch_ms")
        or timing.get("capture_epoch_ms")
        or timing.get("overlay_done_ms")
    )
    published_age_sec = None
    if published_epoch_ms and published_epoch_ms > 1_000_000_000_000:
        published_age_sec = max(0.0, time.time() - (float(published_epoch_ms) / 1000.0))
    elif number(perf.get("generated_epoch")):
        published_age_sec = max(0.0, time.time() - float(number(perf.get("generated_epoch"), 0.0) or 0.0))
    capture_epoch_source = ""
    last_capture_epoch = None
    for source_name, source_value in (
        ("timing_trace.display_published_epoch_ms", timing.get("display_published_epoch_ms")),
        ("timing_trace.overlay_done_ms", timing.get("overlay_done_ms")),
        ("timing_trace.capture_epoch_ms", timing.get("capture_epoch_ms")),
        ("timing_trace.state_published_ms", timing.get("state_published_ms")),
    ):
        candidate_epoch_ms = number(source_value)
        if candidate_epoch_ms and candidate_epoch_ms > 1_000_000_000_000:
            last_capture_epoch = float(candidate_epoch_ms) / 1000.0
            capture_epoch_source = source_name
            break
    if last_capture_epoch is None:
        for source_name, source_value in (
            ("live.last_capture_epoch", live.get("last_capture_epoch")),
            ("live.display_published_epoch", live.get("display_published_epoch")),
            ("live.last_display_published_epoch", live.get("last_display_published_epoch")),
            ("live.model_capture_epoch", live.get("model_capture_epoch")),
            ("live.display_capture_epoch", live.get("display_capture_epoch")),
            ("live.last_display_capture_epoch", live.get("last_display_capture_epoch")),
            ("live.last_capture_started_epoch", live.get("last_capture_started_epoch")),
        ):
            candidate_epoch = number(source_value)
            if candidate_epoch and candidate_epoch > 0:
                last_capture_epoch = float(candidate_epoch)
                capture_epoch_source = source_name
                break
    capture_age_sec = None
    if last_capture_epoch and last_capture_epoch > 0:
        capture_age_sec = max(0.0, time.time() - float(last_capture_epoch))
    tracking_enabled = bool(live.get("tracking_enabled"))
    session_status = text(live.get("status")).upper()
    stale_flags = [text(item).upper() for item in sequence(visual.get("stale_flags")) if text(item)]
    reasons: list[str] = []
    if not bool(live_resp.get("ok")):
        reasons.append("LIVE_ENDPOINT_NOT_OK")
    if not bool(council_resp.get("ok")):
        reasons.append("COUNCIL_ENDPOINT_NOT_OK")
    if not bool(perf_resp.get("ok")):
        reasons.append("PERF_ENDPOINT_NOT_OK")
    if not tracking_enabled:
        reasons.append("TRACKING_DISABLED")
    if session_status not in {"RUNNING", "FRESH", "TRACKING"}:
        reasons.append(f"SESSION_STATUS_{session_status or 'UNKNOWN'}")
    if frame_age_ms is None:
        reasons.append("FRAME_AGE_MISSING")
    elif float(frame_age_ms) > max_frame_age_ms:
        reasons.append(f"FRAME_AGE_{round(float(frame_age_ms), 1)}MS_GT_{round(max_frame_age_ms, 1)}MS")
    published_frame_fresh = bool(
        frame_age_ms is not None
        and float(frame_age_ms) <= max_frame_age_ms
        and stale_status not in {"STALE", "FAIL", "FAILED", "ERROR"}
    )
    published_age_warning = None
    if published_age_sec is not None and float(published_age_sec) > max_capture_age_sec:
        published_age_warning = f"PUBLISHED_AGE_{round(float(published_age_sec), 3)}S_GT_{round(max_capture_age_sec, 3)}S"
        if reject_published_age_warning:
            reasons.append(published_age_warning)
    capture_age_warning = None
    if capture_age_sec is None:
        if not published_frame_fresh:
            reasons.append("CAPTURE_AGE_MISSING")
    elif float(capture_age_sec) > max_capture_age_sec:
        capture_age_warning = f"CAPTURE_START_AGE_{round(float(capture_age_sec), 3)}S_GT_{round(max_capture_age_sec, 3)}S"
        if reject_capture_age_warning or not published_frame_fresh:
            reasons.append(f"CAPTURE_AGE_{round(float(capture_age_sec), 3)}S_GT_{round(max_capture_age_sec, 3)}S")
    if stale_status in {"STALE", "FAIL", "FAILED", "ERROR"}:
        reasons.append(f"STALE_STATUS_{stale_status}")
    if stale_flags:
        reasons.append("VISUAL_STALE_FLAGS_" + ",".join(stale_flags[:4]))
    return {
        "fresh": not reasons,
        "reasons": reasons,
        "max_frame_age_ms": max_frame_age_ms,
        "max_capture_age_sec": max_capture_age_sec,
        "frame_age_ms": frame_age_ms,
        "capture_age_sec": round(float(capture_age_sec), 3) if capture_age_sec is not None else None,
        "capture_epoch_source": capture_epoch_source,
        "published_age_sec": round(float(published_age_sec), 3) if published_age_sec is not None else None,
        "capture_age_warning": capture_age_warning,
        "published_age_warning": published_age_warning,
        "published_frame_fresh": published_frame_fresh,
        "stale_status": stale_status,
        "tracking_enabled": tracking_enabled,
        "session_status": session_status,
        "reject_published_age_warning": reject_published_age_warning,
        "reject_capture_age_warning": reject_capture_age_warning,
    }


def apply_freshness_guard(entry: Mapping[str, Any], freshness: Mapping[str, Any]) -> dict[str, Any]:
    guarded = dict(entry)
    raw_allowed = bool(guarded.get("allowed"))
    raw_authorized = bool(guarded.get("execution_authorized"))
    guarded["raw_allowed_without_freshness_guard"] = raw_allowed
    guarded["raw_execution_authorized_without_freshness_guard"] = raw_authorized
    guarded["freshness"] = dict(freshness)
    if bool(freshness.get("fresh")):
        guarded["freshness_rejected"] = False
        return guarded
    guarded["freshness_rejected"] = bool(
        raw_allowed
        or raw_authorized
        or guarded.get("entry_now_allowed")
        or guarded.get("lane_accepted")
        or guarded.get("packet_present")
    )
    guarded["allowed"] = False
    guarded["execution_authorized"] = False
    guarded["blocked_by"] = "STALE_RUNTIME_GUARD"
    guarded["next_required"] = "Fresh tracker frame required before any entry package can be accepted."
    return guarded


def opportunity_class(entry: Mapping[str, Any], audit: Mapping[str, Any]) -> str:
    blocked = text(entry.get("blocked_by") or audit.get("top_blocker") or audit.get("denied_at")).upper()
    timing = text(entry.get("timing_mode")).upper()
    if bool(entry.get("allowed")) and bool(entry.get("execution_authorized")):
        return "MATURE_OPPORTUNITY"
    if bool(entry.get("allowed")):
        return "TIMING_ALLOWED_STUDY_ONLY"
    if "LATE" in blocked or timing == "SKIP_LATE_ENTRY":
        return "LATE_OPPORTUNITY"
    if any(token in blocked for token in ("TRAP", "BAD_ENTRY", "OPPOSING", "NO_PATH")):
        return "DANGEROUS_OPPORTUNITY"
    if timing.startswith("WAIT") or "WAIT" in blocked:
        return "EARLY_OPPORTUNITY"
    return "NOT_AN_OPPORTUNITY"


def blocked_trend_aligned_study(entry: Mapping[str, Any]) -> dict[str, Any]:
    blocked = text(entry.get("blocked_by")).upper()
    if bool(entry.get("allowed")):
        return {"active": False, "reason": "already_allowed"}
    if side(entry.get("side")) not in {"BUY", "SELL"}:
        return {"active": False, "reason": "no_trade_side"}
    if not bool(entry.get("entry_now_allowed")) or not bool(entry.get("lane_accepted")):
        return {"active": False, "reason": "not_enter_now_lane_accepted"}
    if bool(entry.get("freshness_rejected")) or "STALE" in blocked:
        return {"active": False, "reason": "freshness_or_stale_guard"}
    if bool(entry.get("hard_bad_entry_class_active")):
        return {"active": False, "reason": "hard_bad_entry_class"}
    if entry.get("directional_location_ok") is False or bool(entry.get("directional_location_chase_risk")):
        return {"active": False, "reason": "directional_location_guard"}
    if any(token in blocked for token in HARD_BLOCKED_STUDY_TOKENS):
        return {"active": False, "reason": f"hard_blocker:{blocked}"}
    if blocked and blocked not in SOFT_BLOCKED_STUDY_REASONS:
        return {"active": False, "reason": f"non_soft_blocker:{blocked}"}
    return {
        "active": True,
        "reason": blocked or "SOFT_BLOCKED_ENTER_NOW",
        "side": side(entry.get("side")),
        "lane_name": text(entry.get("lane_name")).upper(),
        "timing_mode": text(entry.get("timing_mode")).upper(),
        "final_score": entry.get("final_score"),
        "threshold": entry.get("threshold"),
    }


def manual_entry_rearm_decision(
    entry: Mapping[str, Any],
    sample: Mapping[str, Any],
    state: dict[str, dict[str, Any]],
    observed_at: float,
) -> dict[str, Any]:
    if not bool(entry.get("allowed")):
        return {
            "allowed": False,
            "suppressed": False,
            "reason": "not_allowed_entry",
            "key": "",
        }
    min_sec = float(os.getenv("PHOENIXGUARD_MANUAL_ENTRY_REARM_MIN_SEC", "300") or "300")
    min_price_px = float(os.getenv("PHOENIXGUARD_MANUAL_ENTRY_REARM_MIN_PRICE_PX", "36") or "36")
    min_frame_delta = int(float(os.getenv("PHOENIXGUARD_MANUAL_ENTRY_REARM_MIN_FRAME_DELTA", "45") or "45"))
    entry_side = side(entry.get("side"))
    lane_name = text(entry.get("lane_name"), "UNKNOWN").upper()
    candidate_id = text(entry.get("candidate_id") or entry.get("packet_id") or "candidate_unknown")
    base_key = "|".join([candidate_id, entry_side, lane_name])
    frame_id = int(number(mapping(sample.get("frames")).get("display_frame_id"), 0) or 0)
    price_y = number(mapping(sample.get("price_proxy")).get("current_y"))
    last = state.get(base_key)
    if not last:
        state[base_key] = {"at": observed_at, "frame": frame_id, "price_y": price_y, "generation": 1}
        return {
            "allowed": True,
            "suppressed": False,
            "reason": "first_rearmed_entry_for_candidate_lane",
            "key": f"{base_key}|rearm=1",
            "base_key": base_key,
            "generation": 1,
            "min_rearm_sec": min_sec,
            "min_rearm_price_px": min_price_px,
            "min_rearm_frame_delta": min_frame_delta,
        }
    elapsed = max(0.0, observed_at - float(number(last.get("at"), observed_at) or observed_at))
    frame_delta = max(0, frame_id - int(number(last.get("frame"), frame_id) or frame_id))
    last_price = number(last.get("price_y"))
    price_delta = abs(float(price_y) - float(last_price)) if price_y is not None and last_price is not None else 0.0
    rearmed = bool(elapsed >= min_sec and (price_delta >= min_price_px or frame_delta >= min_frame_delta))
    if rearmed:
        generation = int(number(last.get("generation"), 1) or 1) + 1
        state[base_key] = {"at": observed_at, "frame": frame_id, "price_y": price_y, "generation": generation}
        return {
            "allowed": True,
            "suppressed": False,
            "reason": "candidate_rearmed_after_time_and_new_price_or_frame",
            "key": f"{base_key}|rearm={generation}",
            "base_key": base_key,
            "generation": generation,
            "elapsed_sec": round(elapsed, 3),
            "frame_delta": frame_delta,
            "price_delta_px": round(price_delta, 3),
            "min_rearm_sec": min_sec,
            "min_rearm_price_px": min_price_px,
            "min_rearm_frame_delta": min_frame_delta,
        }
    return {
        "allowed": False,
        "suppressed": True,
        "reason": "duplicate_candidate_lane_suppressed_until_rearm",
        "key": f"{base_key}|rearm={int(number(last.get('generation'), 1) or 1)}",
        "base_key": base_key,
        "generation": int(number(last.get("generation"), 1) or 1),
        "elapsed_sec": round(elapsed, 3),
        "frame_delta": frame_delta,
        "price_delta_px": round(price_delta, 3),
        "min_rearm_sec": min_sec,
        "min_rearm_price_px": min_price_px,
        "min_rearm_frame_delta": min_frame_delta,
    }


def no_silent_failure_status(
    live_resp: Mapping[str, Any],
    council_resp: Mapping[str, Any],
    perf_resp: Mapping[str, Any],
    live: Mapping[str, Any],
    council: Mapping[str, Any],
    perf: Mapping[str, Any],
) -> dict[str, Any]:
    visual = mapping(live.get("visual_health_v3") or perf.get("visual_health"))
    model = mapping(live.get("model_state") or perf.get("model_health_summary"))
    promotion = mapping(council.get("promotion_trace"))
    broker = mapping(live.get("broker_source") or mapping(live.get("tracking_summary")).get("broker_source"))

    def endpoint(name: str, resp: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "status": "PASS" if bool(resp.get("ok")) else "FAIL",
            "reason": text(resp.get("error"), "endpoint responded"),
            "latency_ms": round(number(resp.get("latency_ms"), 0.0) or 0.0, 3),
        }

    return {
        "schema_version": "PG_NO_SILENT_FAILURE_TRACE_V1",
        "live_endpoint": endpoint("live", live_resp),
        "council_endpoint": endpoint("council", council_resp),
        "performance_endpoint": endpoint("perf", perf_resp),
        "visual_health": {
            "status": text(visual.get("status"), "NOT_APPLICABLE").upper(),
            "reason": ",".join(str(item) for item in sequence(visual.get("stale_flags"))) or "visual health reported",
        },
        "model_state": {
            "status": "PASS" if "awake" in text(model.get("label")).lower() or model.get("all_required_models_awake") is True else "STALE",
            "reason": text(model.get("label"), "model health summary not present"),
            "queue_depth": number(model.get("queue_depth"), 0.0),
        },
        "broker_source": {
            "status": "PASS" if bool(broker.get("broker_click_safe") or broker.get("valid")) else ("NOT_APPLICABLE" if not broker else "FAIL"),
            "reason": text(broker.get("reason"), "broker source not present on this route"),
        },
        "promotion_audit": {
            "status": "PASS" if mapping(promotion.get("promotion_failure_audit_v3")) else "NOT_APPLICABLE",
            "reason": "promotion_failure_audit_v3 present" if mapping(promotion.get("promotion_failure_audit_v3")) else "no promotion audit on this sample",
        },
    }


def compact_sample(seq: int, live_resp: Mapping[str, Any], council_resp: Mapping[str, Any], perf_resp: Mapping[str, Any], session_id: str) -> dict[str, Any]:
    live = mapping(live_resp.get("json"))
    council = mapping(council_resp.get("json"))
    perf = mapping(perf_resp.get("json"))
    thesis = mapping(live.get("signal_thesis_v3"))
    promotion = mapping(council.get("promotion_trace"))
    lane = mapping(promotion.get("execution_lane"))
    timing = mapping(promotion.get("timing_decision"))
    audit = mapping(council.get("promotion_failure_audit_v3") or promotion.get("promotion_failure_audit_v3"))
    cycle = mapping(lane.get("high_frequency_candle_cycle") or promotion.get("high_frequency_candle_cycle"))
    next1 = mapping(cycle.get("next_candle_forecast"))
    next2 = mapping(cycle.get("second_next_candle_forecast"))
    visual = mapping(live.get("visual_health_v3") or perf.get("visual_health"))
    model = mapping(live.get("model_state") or perf.get("model_health_summary"))
    overlays = sequence(live.get("overlay_objects"))
    market_identity = {
        "name": text(live.get("name")),
        "market": text(live.get("market")),
        "locked_title": text(live.get("locked_title")),
        "symbol": text(thesis.get("symbol") or thesis.get("symbol_key")),
        "timeframe": text(thesis.get("timeframe")),
    }
    trendlines = [
        row for row in overlays
        if "TRENDLINE" in text(mapping(row).get("type") or mapping(row).get("label") or mapping(row).get("role")).upper()
    ]
    freshness = runtime_freshness_state(live_resp, council_resp, perf_resp, live, perf)
    entry = apply_freshness_guard(entry_state(live, council), freshness)
    blocked_study = blocked_trend_aligned_study(entry)
    entry["blocked_trend_aligned_study"] = bool(blocked_study.get("active"))
    entry["blocked_trend_aligned_reason"] = text(blocked_study.get("reason"))
    opportunity = opportunity_class(entry, audit)
    return {
        "schema_version": SCHEMA_VERSION,
        "seq": seq,
        "captured_at_utc": utc_now(),
        "captured_epoch": time.time(),
        "runtime": {
            "live_ok": bool(live_resp.get("ok")),
            "council_ok": bool(council_resp.get("ok")),
            "perf_ok": bool(perf_resp.get("ok")),
            "live_latency_ms": round(number(live_resp.get("latency_ms"), 0.0) or 0.0, 3),
            "council_latency_ms": round(number(council_resp.get("latency_ms"), 0.0) or 0.0, 3),
            "perf_latency_ms": round(number(perf_resp.get("latency_ms"), 0.0) or 0.0, 3),
            "live_error": text(live_resp.get("error")),
            "council_error": text(council_resp.get("error")),
            "perf_error": text(perf_resp.get("error")),
        },
        "frames": {
            "display_frame_id": live.get("display_frame_id"),
            "overlay_frame_id": live.get("overlay_frame_id"),
            "model_vote_frame_id": live.get("model_vote_frame_id"),
            "capture_count": live.get("capture_count"),
            "status": live.get("status"),
            "tracking_enabled": live.get("tracking_enabled"),
        },
        "freshness": freshness,
        "market_identity": market_identity,
        "entry": entry,
        "grade_a_star_audit": {
            "schema_version": "PG_GRADE_A_STAR_BURN_AUDIT_V1",
            "promotion_failure_audit_v3": audit,
            "execution_opportunity": {
                "class": opportunity,
                "side": entry.get("side"),
                "can_publish_execution_packet": bool(entry.get("execution_authorized")),
                "reason": text(entry.get("next_required") or audit.get("next_required") or entry.get("blocked_by"), opportunity),
            },
            "blocked_trend_aligned_study": blocked_study,
            "timing_decision": timing,
            "execution_lane": lane,
            "next_candle_forecast": {
                "next1": next1,
                "next2": next2,
            },
            "no_silent_failure": no_silent_failure_status(live_resp, council_resp, perf_resp, live, council, perf),
        },
        "price_proxy": {
            "current_y": number(thesis.get("current_price_proxy")),
            "entry_y": number(thesis.get("entry_price_proxy")),
            "progress_norm": number(thesis.get("move_progress_norm") or thesis.get("unrealized_progress_norm")),
            "target_reached": bool(thesis.get("target_reached")),
            "invalidated": bool(thesis.get("invalidated")),
        },
        "study_quality": {
            "renderable_count": live.get("renderable_count"),
            "overlay_count": live.get("overlay_count"),
            "trendline_count": len(trendlines),
        },
        "health": {
            "visual": visual,
            "model": model,
            "artifacts": artifact_health(session_id) if seq == 1 or seq % 12 == 0 else {},
            "disk": disk_health() if seq == 1 or seq % 12 == 0 else {},
        },
    }


def score_events(
    samples: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    *,
    include_blocked_trend_study: bool = False,
) -> dict[str, Any]:
    horizons = [60, 300, 600, 900]
    scores: dict[str, Any] = {}
    for horizon in horizons:
        rows: list[dict[str, Any]] = []
        for event in entries:
            entry_payload = mapping(event.get("entry"))
            included = (
                bool(entry_payload.get("blocked_trend_aligned_study"))
                if include_blocked_trend_study
                else entry_payload.get("allowed") is True
            )
            if not included:
                continue
            seq = int(event.get("seq", 0) or 0)
            base = next((sample for sample in samples if int(sample.get("seq", -1)) == seq), None)
            if not base:
                continue
            start_y = number(mapping(base.get("price_proxy")).get("current_y"))
            entry_side = side(mapping(base.get("entry")).get("side") or entry_payload.get("side"))
            if start_y is None or entry_side not in {"BUY", "SELL"}:
                continue
            target_time = float(base.get("captured_epoch", 0.0) or 0.0) + horizon
            future = next((sample for sample in samples if float(sample.get("captured_epoch", 0.0) or 0.0) >= target_time), None)
            if not future:
                continue
            future_y = number(mapping(future.get("price_proxy")).get("current_y"))
            if future_y is None:
                continue
            delta = future_y - start_y
            if abs(delta) <= 3.0:
                verdict = "flat"
            elif entry_side == "BUY":
                verdict = "correct" if delta < -3.0 else "wrong"
            else:
                verdict = "correct" if delta > 3.0 else "wrong"
            rows.append(
                {
                    "seq": seq,
                    "side": entry_side,
                    "start_y": start_y,
                    "future_y": future_y,
                    "delta_y": delta,
                    "verdict": verdict,
                    "lane": entry_payload.get("lane_name"),
                    "blocked_by": entry_payload.get("blocked_by"),
                    "blocked_trend_aligned_study": bool(entry_payload.get("blocked_trend_aligned_study")),
                }
            )
        scores[str(horizon)] = {"counts": dict(Counter(row["verdict"] for row in rows)), "rows": rows}
    return scores


def score_entries(samples: list[dict[str, Any]], entries: list[dict[str, Any]]) -> dict[str, Any]:
    return score_events(samples, entries, include_blocked_trend_study=False)


def entry_score_lookup(scores: Mapping[str, Any], seq: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon, payload in scores.items():
        rows = sequence(mapping(payload).get("rows"))
        row: dict[str, Any] = {}
        for item in rows:
            candidate = mapping(item)
            if int(candidate.get("seq", -1) or -1) == seq:
                row = candidate
                break
        if row:
            result[str(horizon)] = {
                "verdict": row.get("verdict"),
                "start_y": row.get("start_y"),
                "future_y": row.get("future_y"),
                "delta_y": row.get("delta_y"),
            }
    return result


def write_entry_gallery(out_dir: Path, entries: list[dict[str, Any]], scores: Mapping[str, Any]) -> Path:
    manifest: list[dict[str, Any]] = []
    for index, event in enumerate(entries, start=1):
        seq = int(event.get("seq", 0) or 0)
        overlay = text(event.get("overlay_evidence_path"))
        broker = text(event.get("broker_evidence_path"))
        manifest.append(
            {
                "index": index,
                "seq": seq,
                "frame": event.get("frame"),
                "side": event.get("side"),
                "captured_at_utc": event.get("captured_at_utc"),
                "overlay_evidence_path": overlay,
                "broker_evidence_path": broker,
                "chart_point": event.get("chart_point"),
                "window_point": event.get("window_point"),
                "overlay_source_mode": event.get("overlay_source_mode"),
                "window_source_mode": event.get("window_source_mode"),
                "overlay_freshness": event.get("overlay_freshness"),
                "window_freshness": event.get("window_freshness"),
                "evidence_reason": event.get("evidence_reason"),
                "blocked_entry_capture": bool(event.get("blocked_entry_capture")),
                "execution_authorized": event.get("execution_authorized"),
                "packet_present": event.get("packet_present"),
                "entry": event.get("entry"),
                "outcome_scores": entry_score_lookup(scores, seq),
            }
        )
    write_json(out_dir / "entry_sequence_manifest.json", {"schema_version": "PG_ENTRY_SEQUENCE_MANIFEST_V1", "entries": manifest})

    def rel(raw: str) -> str:
        if not raw:
            return ""
        try:
            return Path(raw).resolve().relative_to(out_dir.resolve()).as_posix()
        except Exception:
            return Path(raw).as_posix()

    lines = [
        "<!doctype html>",
        "<html><head><meta charset=\"utf-8\"><title>PhoenixGuard Entry Allowance Gallery</title>",
        "<style>body{margin:0;background:#10100e;color:#f3f0e8;font-family:Segoe UI,Arial,sans-serif;}main{padding:18px;}section{border-top:1px solid #333;padding:16px 0;}h1{font-size:22px}h2{font-size:16px}img{max-width:48%;height:auto;border:1px solid #444;margin-right:12px;vertical-align:top}.meta{color:#b8b0a2;font-size:12px;margin-bottom:10px}</style>",
        "</head><body><main>",
        "<h1>PhoenixGuard Entry Allowance Sequence</h1>",
    ]
    if not entries:
        lines.append("<p>No entry allowance screenshots were captured.</p>")
    for event in entries:
        seq = int(event.get("seq", 0) or 0)
        blocked = bool(event.get("blocked_entry_capture"))
        reason = text(event.get("evidence_reason"), "entry_evidence")
        lines.append("<section>")
        lines.append(f"<h2>seq {seq} frame {event.get('frame')} {event.get('side')} {'BLOCKED' if blocked else 'ALLOWED'}</h2>")
        lines.append(
            "<div class=\"meta\">"
            f"{event.get('captured_at_utc')} | chart {mapping(event.get('chart_point')).get('x')},"
            f"{mapping(event.get('chart_point')).get('y')} | window {mapping(event.get('window_point')).get('x')},"
            f"{mapping(event.get('window_point')).get('y')} | reason {reason} | overlay source {event.get('overlay_source_mode', 'unknown')}"
            "</div>"
        )
        overlay = rel(text(event.get("overlay_evidence_path")))
        broker = rel(text(event.get("broker_evidence_path")))
        if overlay:
            lines.append(f"<img src=\"{overlay}\" alt=\"overlay entry evidence\">")
        if broker:
            lines.append(f"<img src=\"{broker}\" alt=\"broker entry evidence\">")
        outcome = entry_score_lookup(scores, seq)
        if outcome:
            lines.append(f"<pre>{json.dumps(outcome, indent=2, ensure_ascii=True)}</pre>")
        lines.append("</section>")
    lines.append("</main></body></html>")
    path = out_dir / "entry_gallery.html"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_report(out_dir: Path, samples: list[dict[str, Any]], entries: list[dict[str, Any]], storage_events: list[dict[str, Any]]) -> None:
    duration_min = 0.0
    if samples:
        duration_min = (float(samples[-1].get("captured_epoch", 0.0)) - float(samples[0].get("captured_epoch", 0.0))) / 60.0
    latencies = {
        name: [number(mapping(sample.get("runtime")).get(f"{name}_latency_ms"), 0.0) or 0.0 for sample in samples]
        for name in ("live", "council", "perf")
    }
    def p95(values: list[float]) -> float:
        values = sorted(values)
        if not values:
            return 0.0
        return values[min(len(values) - 1, int(math.ceil(len(values) * 0.95)) - 1)]
    scores = score_entries(samples, entries)
    blocked_trend_scores = score_events(samples, entries, include_blocked_trend_study=True)
    gallery_path = write_entry_gallery(out_dir, entries, scores)
    allowed_entry_events = [event for event in entries if mapping(event.get("entry")).get("allowed") is True]
    blocked_enter_now_events = [event for event in entries if bool(event.get("blocked_entry_capture"))]
    manual_alert_events = [event for event in allowed_entry_events if bool(event.get("manual_alert_allowed"))]
    suppressed_allowed_events = [event for event in allowed_entry_events if bool(event.get("manual_alert_suppressed"))]
    blocked_trend_study_events = [
        event
        for event in entries
        if bool(mapping(event.get("entry")).get("blocked_trend_aligned_study"))
    ]
    manual_alert_sample_count = sum(1 for sample in samples if bool(mapping(sample.get("entry")).get("manual_alert_allowed")))
    suppressed_allowed_sample_count = sum(1 for sample in samples if bool(mapping(sample.get("entry")).get("manual_alert_suppressed")))
    blocked_trend_study_sample_count = sum(1 for sample in samples if bool(mapping(sample.get("entry")).get("blocked_trend_aligned_study")))
    blocker_counts: Counter[str] = Counter()
    opportunity_counts: Counter[str] = Counter()
    component_counts: Counter[str] = Counter()
    for sample in samples:
        audit = mapping(mapping(sample.get("grade_a_star_audit")).get("promotion_failure_audit_v3"))
        if audit:
            blocker_counts[text(audit.get("top_blocker") or audit.get("denied_at"), "UNKNOWN")] += 1
        opportunity = mapping(mapping(sample.get("grade_a_star_audit")).get("execution_opportunity"))
        if opportunity:
            opportunity_counts[text(opportunity.get("class"), "UNKNOWN")] += 1
        no_silent = mapping(mapping(sample.get("grade_a_star_audit")).get("no_silent_failure"))
        for name, component in no_silent.items():
            component_map = mapping(component)
            status = text(component_map.get("status"))
            if status:
                component_counts[f"{name}:{status}"] += 1
    identities = [
        mapping(sample.get("market_identity"))
        for sample in samples
        if mapping(sample.get("market_identity"))
    ]
    market_values = sorted({text(row.get("market") or row.get("symbol") or row.get("name")) for row in identities if text(row.get("market") or row.get("symbol") or row.get("name"))})
    locked_titles = sorted({text(row.get("locked_title")) for row in identities if text(row.get("locked_title"))})
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sample_count": len(samples),
        "entry_event_count": len(entries),
        "allowed_entry_event_count": len(allowed_entry_events),
        "blocked_enter_now_event_count": len(blocked_enter_now_events),
        "duration_min": round(duration_min, 2),
        "latency": {
            name: {"p50_ms": sorted(values)[len(values) // 2] if values else 0.0, "p95_ms": p95(values), "max_ms": max(values) if values else 0.0}
            for name, values in latencies.items()
        },
        "entry_scores": scores,
        "blocked_trend_aligned_scores": blocked_trend_scores,
        "storage_events": storage_events,
        "entry_gallery_path": str(gallery_path),
        "manual_alert_event_count": len(manual_alert_events),
        "manual_alert_sample_count": manual_alert_sample_count,
        "suppressed_allowed_duplicate_event_count": len(suppressed_allowed_events),
        "suppressed_allowed_duplicate_count": suppressed_allowed_sample_count,
        "blocked_trend_aligned_study_event_count": len(blocked_trend_study_events),
        "blocked_trend_aligned_study_sample_count": blocked_trend_study_sample_count,
        "blocker_counts": dict(blocker_counts),
        "opportunity_counts": dict(opportunity_counts),
        "component_status_counts": dict(component_counts),
        "market_identity": {
            "values": market_values,
            "locked_title_count": len(locked_titles),
            "pair_switch_detected": len(market_values) > 1,
        },
    }
    write_json(out_dir / "analysis_summary.json", summary)
    lines = [
        "# PhoenixGuard Entry-Allowance Burn Report",
        "",
        f"Window: {duration_min:.1f} min, samples: {len(samples)}, entry events: {len(entries)}.",
        f"Allowed entry evidence events: {len(allowed_entry_events)}. Blocked ENTER_NOW evidence events: {len(blocked_enter_now_events)}.",
        f"Manual alert allowed observations: {manual_alert_sample_count}. Suppressed duplicate allowed observations: {suppressed_allowed_sample_count}.",
        f"Blocked trend-aligned study observations: {blocked_trend_study_sample_count}. Captured study events: {len(blocked_trend_study_events)}.",
        "",
        "## Entry Evidence",
        f"- Gallery: {gallery_path}",
        f"- Manifest: {out_dir / 'entry_sequence_manifest.json'}",
    ]
    if entries:
        for event in entries[:80]:
            lines.append(
                f"- seq {event.get('seq')} frame {event.get('frame')}: {event.get('side')} at chart "
                f"{mapping(event.get('chart_point')).get('x')},{mapping(event.get('chart_point')).get('y')} -> "
                f"{event.get('overlay_evidence_path')}"
            )
        if len(entries) > 80:
            lines.append(f"- ... {len(entries) - 80} more events in analysis_summary.json")
    else:
        lines.append("- No executable entry allowance events were captured in this burn window.")
    lines += [
        "",
        "## Horizon Scores",
    ]
    for horizon, payload in scores.items():
        lines.append(f"- {int(horizon) // 60} min: {payload.get('counts', {})}")
    lines += [
        "",
        "## Blocked Trend-Aligned Study Scores",
    ]
    for horizon, payload in blocked_trend_scores.items():
        lines.append(f"- {int(horizon) // 60} min: {payload.get('counts', {})}")
    lines += [
        "",
        "## Grade A* Audit Snapshot",
        f"- Opportunity classes: {dict(opportunity_counts)}",
        f"- Top promotion blockers: {dict(blocker_counts)}",
        f"- Component statuses: {dict(component_counts)}",
        "",
        "## Runtime",
        f"- Market identities seen: {market_values or ['unknown']}. Pair switch detected: {summary['market_identity']['pair_switch_detected']}.",
        f"- Live latency p95/max: {summary['latency']['live']['p95_ms']:.1f} / {summary['latency']['live']['max_ms']:.1f} ms.",
        f"- Council latency p95/max: {summary['latency']['council']['p95_ms']:.1f} / {summary['latency']['council']['max_ms']:.1f} ms.",
        f"- Perf latency p95/max: {summary['latency']['perf']['p95_ms']:.1f} / {summary['latency']['perf']['max_ms']:.1f} ms.",
    ]
    if storage_events:
        lines.append("")
        lines.append("## Storage Guard")
        for event in storage_events[-12:]:
            lines.append(f"- {event.get('at_utc')}: {event}")
    (out_dir / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bounded entry-allowance burn and capture annotated entry evidence.")
    parser.add_argument("--base-url", default=os.getenv("PHOENIXGUARD_BURN_BASE_URL", "http://127.0.0.1:8793"))
    parser.add_argument("--session-id", default=os.getenv("PHOENIXGUARD_BURN_SESSION_ID", "pocket-live-8788"))
    parser.add_argument("--duration-sec", type=float, default=float(os.getenv("PHOENIXGUARD_BURN_DURATION_SEC", "7200") or "7200"))
    parser.add_argument("--interval-sec", type=float, default=float(os.getenv("PHOENIXGUARD_BURN_INTERVAL_SEC", "5") or "5"))
    parser.add_argument("--timeout-sec", type=float, default=float(os.getenv("PHOENIXGUARD_BURN_TIMEOUT_SEC", "30") or "30"))
    parser.add_argument("--storage-guard-interval-sec", type=float, default=float(os.getenv("PHOENIXGUARD_BURN_STORAGE_GUARD_INTERVAL_SEC", "60") or "60"))
    parser.add_argument("--raw-every-sec", type=float, default=float(os.getenv("PHOENIXGUARD_BURN_RAW_EVERY_SEC", "60") or "60"))
    parser.add_argument(
        "--entry-evidence-min-sec",
        type=float,
        default=float(os.getenv("PHOENIXGUARD_BURN_ENTRY_EVIDENCE_MIN_SEC", "60") or "60"),
        help="Minimum seconds between screenshots for the same continuous entry-allowance episode.",
    )
    parser.add_argument(
        "--max-frame-age-ms",
        type=float,
        default=float(os.getenv("PHOENIXGUARD_BURN_MAX_FRAME_AGE_MS", "2500") or "2500"),
        help="Maximum allowed tracker frame age before entry observations are hard-blocked as stale.",
    )
    parser.add_argument(
        "--clear-existing",
        dest="clear_existing",
        action="store_true",
        default=os.getenv("PHOENIXGUARD_BURN_CLEAR_EXISTING", "1") != "0",
        help="Delete old hardening study folders before this burn starts.",
    )
    parser.add_argument(
        "--keep-existing",
        dest="clear_existing",
        action="store_false",
        help="Keep old hardening study folders before this burn starts.",
    )
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()
    os.environ["PHOENIXGUARD_BURN_MAX_FRAME_AGE_MS"] = str(float(args.max_frame_age_ms))

    retention_age_sec = int(max(7200.0, float(args.duration_sec) + 1800.0))
    os.environ.setdefault("PHOENIXGUARD_BURN_ENTRY_EVIDENCE_MAX_AGE_SEC", str(retention_age_sec))
    os.environ.setdefault("PHOENIXGUARD_BURN_RAW_MAX_AGE_SEC", str(retention_age_sec))
    os.environ.setdefault("PHOENIXGUARD_ENTRY_EVIDENCE_MAX_AGE_SEC", str(retention_age_sec))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    preflight_cleanup: dict[str, Any] = clear_existing_hardening_studies() if bool(args.clear_existing) and not args.out_dir else {
        "path": str(local_root() / "hardening_studies"),
        "status": "kept",
        "removed": 0,
        "removed_mb": 0.0,
    }
    out_dir = Path(args.out_dir) if args.out_dir else local_root() / "hardening_studies" / f"entry_allowance_burn_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    samples_path = out_dir / "samples.jsonl"
    entries_path = out_dir / "entry_events.jsonl"
    storage_path = out_dir / "storage_events.jsonl"
    status_path = out_dir / "status.json"
    raw_dir = out_dir / "raw"
    started = time.time()
    end = started + max(1.0, float(args.duration_sec))
    capture_blocked_enter_now = os.getenv("PHOENIXGUARD_BURN_CAPTURE_BLOCKED_ENTER_NOW", "1") != "0"
    write_json(
        out_dir / "baseline.json",
        {
            "schema_version": SCHEMA_VERSION,
            "started_at_utc": utc_now(),
            "started_epoch": started,
            "duration_sec": args.duration_sec,
            "interval_sec": args.interval_sec,
            "storage_guard_interval_sec": args.storage_guard_interval_sec,
            "raw_every_sec": args.raw_every_sec,
            "entry_evidence_min_sec": args.entry_evidence_min_sec,
            "max_frame_age_ms": args.max_frame_age_ms,
            "capture_blocked_enter_now": capture_blocked_enter_now,
            "preflight_cleanup": preflight_cleanup,
            "base_url": args.base_url,
            "session_id": args.session_id,
            "out_dir": str(out_dir),
        },
    )
    seen_entries: set[str] = set()
    entry_capture_times: dict[str, float] = {}
    entry_allowed_observations = 0
    samples: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    storage_events: list[dict[str, Any]] = []
    seq = 0
    enter_now_observations = 0
    blocked_enter_now_evidence_count = 0
    manual_alert_observations = 0
    manual_alert_suppressed_observations = 0
    blocked_trend_aligned_study_observations = 0
    last_storage = 0.0
    last_raw = 0.0
    pixel_freeze_state: dict[str, Any] = {}
    manual_rearm_state: dict[str, dict[str, Any]] = {}
    try:
        while time.time() < end:
            loop_started = time.time()
            seq += 1
            live_resp, council_resp, perf_resp = fetch_runtime_bundle(args.base_url, args.session_id, args.timeout_sec)
            sample = compact_sample(seq, live_resp, council_resp, perf_resp, args.session_id)
            live = mapping(live_resp.get("json"))
            council = mapping(council_resp.get("json"))
            perf = mapping(perf_resp.get("json"))
            pixel_state = update_pixel_freeze_state(pixel_freeze_state, live, loop_started)
            sample = apply_pixel_freeze_guard(sample, pixel_state)
            samples.append(sample)
            entry = mapping(sample.get("entry"))
            if args.raw_every_sec > 0 and (loop_started - last_raw >= float(args.raw_every_sec) or seq == 1):
                raw_stamp = int(loop_started * 1000.0)
                write_json(raw_dir / f"{seq:05d}_{raw_stamp}_live.json", {"response": live_resp})
                write_json(raw_dir / f"{seq:05d}_{raw_stamp}_council.json", {"response": council_resp})
                write_json(raw_dir / f"{seq:05d}_{raw_stamp}_perf.json", {"response": perf_resp, "visual_health": mapping(perf.get("visual_health"))})
                last_raw = loop_started
            enter_now_observation = bool(entry.get("entry_now_allowed")) and bool(entry.get("lane_accepted"))
            if enter_now_observation:
                enter_now_observations += 1
            allowed_observation = bool(entry.get("allowed"))
            manual_alert = manual_entry_rearm_decision(entry, sample, manual_rearm_state, loop_started)
            sample["entry"]["manual_alert_allowed"] = bool(manual_alert.get("allowed"))
            sample["entry"]["manual_alert_suppressed"] = bool(manual_alert.get("suppressed"))
            sample["entry"]["manual_alert_reason"] = text(manual_alert.get("reason"))
            sample["entry"]["manual_alert_key"] = text(manual_alert.get("key"))
            if bool(manual_alert.get("allowed")):
                manual_alert_observations += 1
            if bool(manual_alert.get("suppressed")):
                manual_alert_suppressed_observations += 1
            blocked_trend_aligned_observation = bool(entry.get("blocked_trend_aligned_study"))
            if blocked_trend_aligned_observation:
                blocked_trend_aligned_study_observations += 1
            entry = mapping(sample.get("entry"))
            append_jsonl(samples_path, sample)
            blocked_enter_now_observation = bool(capture_blocked_enter_now and enter_now_observation and not allowed_observation)
            should_capture_evidence = bool(manual_alert.get("allowed")) or blocked_enter_now_observation
            packet_episode_id = str(entry.get("packet_id") or "") if allowed_observation else ""
            alert_episode_id = text(manual_alert.get("key"))
            entry_key = "|".join(
                [
                    str(entry.get("side") or ""),
                    str(entry.get("timing_mode") or ""),
                    str(entry.get("allowance_mode") or ""),
                    f"allowed={bool(entry.get('allowed'))}",
                    f"packet={bool(entry.get('packet_present'))}",
                    f"packet_id={packet_episode_id}",
                    f"manual_alert_key={alert_episode_id}",
                    f"blocked_trend_aligned={blocked_trend_aligned_observation}",
                    str(entry.get("blocked_by") or ""),
                ]
            )
            if allowed_observation:
                entry_allowed_observations += 1
            last_entry_capture = entry_capture_times.get(entry_key, 0.0)
            first_episode_capture = entry_key not in seen_entries
            periodic_episode_capture = bool(
                should_capture_evidence
                and (
                    float(args.entry_evidence_min_sec) <= 0
                    or loop_started - last_entry_capture >= float(args.entry_evidence_min_sec)
                )
            )
            if should_capture_evidence and (first_episode_capture or periodic_episode_capture):
                seen_entries.add(entry_key)
                entry_capture_times[entry_key] = loop_started
                event: dict[str, Any] = capture_entry_evidence(out_dir, sample, live, council, args.session_id, args.base_url, args.timeout_sec)
                if not event:
                    event = {
                        "seq": seq,
                        "frame": mapping(sample.get("frames")).get("display_frame_id"),
                        "side": entry.get("side"),
                        "captured_at_utc": sample.get("captured_at_utc"),
                        "error": "enter_now_evidence_not_captured",
                    }
                event["entry_episode_key"] = entry_key
                event["manual_alert"] = manual_alert
                event["manual_alert_allowed"] = bool(manual_alert.get("allowed"))
                event["manual_alert_suppressed"] = bool(manual_alert.get("suppressed"))
                event["manual_alert_key"] = text(manual_alert.get("key"))
                event["blocked_entry_capture"] = bool(blocked_enter_now_observation)
                event["blocked_trend_aligned_study_capture"] = bool(blocked_trend_aligned_observation)
                event["execution_authorized"] = bool(entry.get("execution_authorized"))
                event["packet_present"] = bool(entry.get("packet_present"))
                event["evidence_reason"] = (
                    "blocked_enter_now_observation"
                    if blocked_enter_now_observation
                    else (
                        "manual_alert_rearmed_entry"
                        if bool(manual_alert.get("allowed"))
                        else "new_entry_episode"
                        if first_episode_capture
                        else ("allowed_entry_observation" if float(args.entry_evidence_min_sec) <= 0 else "periodic_entry_episode_checkpoint")
                    )
                )
                event["entry_allowed_observations"] = entry_allowed_observations
                event["enter_now_observations"] = enter_now_observations
                event["manual_alert_observations"] = manual_alert_observations
                event["manual_alert_suppressed_observations"] = manual_alert_suppressed_observations
                event["blocked_trend_aligned_study_observations"] = blocked_trend_aligned_study_observations
                if blocked_enter_now_observation:
                    blocked_enter_now_evidence_count += 1
                    event["blocked_enter_now_evidence_count"] = blocked_enter_now_evidence_count
                entries.append(event)
                append_jsonl(entries_path, event)
            if loop_started - last_storage >= max(5.0, float(args.storage_guard_interval_sec)) or seq == 1:
                storage = storage_guard(out_dir, args.session_id)
                if os.getenv("PHOENIXGUARD_BURN_PRUNE_HARDENING_STUDIES", "1") != "0":
                    storage["hardening_studies"] = prune_hardening_studies(out_dir)
                else:
                    storage["hardening_studies"] = {"path": str(local_root() / "hardening_studies"), "status": "disabled_by_env"}
                storage_events.append(storage)
                append_jsonl(storage_path, storage)
                last_storage = loop_started
            write_json(
                status_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "running": True,
                    "seq": seq,
                    "sample_count": len(samples),
                    "entry_event_count": len(entries),
                    "entry_allowed_observation_count": entry_allowed_observations,
                    "enter_now_observation_count": enter_now_observations,
                    "blocked_enter_now_evidence_count": blocked_enter_now_evidence_count,
                    "manual_alert_observation_count": manual_alert_observations,
                    "manual_alert_suppressed_observation_count": manual_alert_suppressed_observations,
                    "blocked_trend_aligned_study_observation_count": blocked_trend_aligned_study_observations,
                    "elapsed_sec": round(time.time() - started, 3),
                    "remaining_sec": round(max(0.0, end - time.time()), 3),
                    "last_frame": mapping(sample.get("frames")).get("display_frame_id"),
                    "last_entry": entry,
                    "last_freshness": mapping(sample.get("freshness")),
                    "last_price_y": mapping(sample.get("price_proxy")).get("current_y"),
                    "updated_at_utc": utc_now(),
                    "out_dir": str(out_dir),
                },
            )
            sleep_for = max(0.1, float(args.interval_sec) - (time.time() - loop_started))
            time.sleep(sleep_for)
    finally:
        write_report(out_dir, samples, entries, storage_events)
        write_json(
            status_path,
            {
                "schema_version": SCHEMA_VERSION,
                "running": False,
                "seq": seq,
                "sample_count": len(samples),
                "entry_event_count": len(entries),
                "entry_allowed_observation_count": entry_allowed_observations,
                "enter_now_observation_count": enter_now_observations,
                "blocked_enter_now_evidence_count": blocked_enter_now_evidence_count,
                "manual_alert_observation_count": manual_alert_observations,
                "manual_alert_suppressed_observation_count": manual_alert_suppressed_observations,
                "blocked_trend_aligned_study_observation_count": blocked_trend_aligned_study_observations,
                "elapsed_sec": round(time.time() - started, 3),
                "remaining_sec": 0.0,
                "updated_at_utc": utc_now(),
                "out_dir": str(out_dir),
                "report_path": str(out_dir / "final_report.md"),
                "summary_path": str(out_dir / "analysis_summary.json"),
                "entry_gallery_path": str(out_dir / "entry_gallery.html"),
            },
        )
    print(json.dumps({"out_dir": str(out_dir), "samples": len(samples), "entry_events": len(entries)}, indent=2))
    return 0


path_key = _path_key


if __name__ == "__main__":
    raise SystemExit(main())
